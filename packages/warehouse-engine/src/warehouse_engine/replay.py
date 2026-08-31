"""事件重放内核：KPI/COGS 计算共享的状态机（docs/formula-spec.md §2/§3）。

口径冻结点（与 formula-spec 0.1.0 一致）：

- 期间归属 ``[start_date, end_date)``：``move_date < start_date`` 为历史事件
  （参与期初重放与成本状态构建），``start_date <= move_date < end_date`` 为本期事件，
  ``move_date >= end_date`` 的事件不参与重放（校验层已发 PERIOD_MISMATCH）；
- 重放顺序：全部参与事件按 ``(move_date, occurred_at, event_id)`` 升序
  （occurred_at 折算 UTC 后去除时区标记，保证混合时区数据可稳定排序），
  同一 SKU 内的相对顺序满足 formula-spec §2.1；
- 冲销（REVERSAL）：沿 ``reversal_of`` 引用链解析到基础事件，按链上冲销层数
  奇偶取反/复原其库存影响（撤销的撤销 = 复原）；撤销作用于基础事件所属
  ``(sku_id, warehouse_id)`` 桶，时点为冲销事件自身的重放位置；本期出库量与
  COGS 的冲减仅当基础事件本身落在分析期内（历史事件的冲销只还原余额，不冲减
  本期口径）；基础事件不在重放范围内（``move_date >= end_date`` 或仓库范围外）
  时冲销不生效（无从撤销未参与重放的事件）；
- 盘点（STOCKTAKE）：重放到该事件时将桶余额替换为实盘数量（quantity），
  替换差额不计入出库量、COGS 与净入库；撤销盘点时还原替换前余额；
- 调拨（TRANSFER）：契约单条记录无法表达调出/调入方向，重放视为 no-op
  （spec §2.3 成对调拨下 SKU 净余额不变、不计入出库量）；
- 负库存：余额照常参与后续计算，桶级首个负余额触发 NEGATIVE_BALANCE Warning
  （fields 依次为 sku_id、warehouse_id、首个负余额日期与数值）；
  期内任一时点（含期初进入时点）余额 < 0 时该 SKU 期间 COGS 记 0（§3.6）；
- COGS 移动加权平均（§3.6）：入库/退货按 ``(balance×avg + in×cost)/(balance+in)``
  更新 avg_cost（分母为 0 跳过；avg 未知时直接取该笔成本）；出库/报废按当前
  avg_cost 计入 COGS（无已知成本记录时该笔记 0 并标记 UNIT_COST_MISSING），
  退货按当前 avg_cost 冲减 COGS。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from warehouse_engine.contracts import (
    AnalysisRequest,
    MovementRecord,
    MoveType,
    Warning,
    WarningSeverity,
)
from warehouse_engine.errors import DataValidationError

#: 入库方向事件（余额 +、参与移动加权平均与单位成本口径）
_INBOUND_TYPES = frozenset({MoveType.INBOUND, MoveType.RETURN})

#: 出库方向事件（余额 −、计入出库量与 COGS）
_OUTBOUND_TYPES = frozenset({MoveType.OUTBOUND, MoveType.SCRAP})


def _normalized_occurred_at(movement: MovementRecord) -> datetime:
    """occurred_at 折算 UTC 后去除时区标记，使混合时区数据可比较排序。"""
    occurred = movement.occurred_at
    if occurred.tzinfo is not None:
        return occurred.astimezone(UTC).replace(tzinfo=None)
    return occurred


def replay_sort_key(movement: MovementRecord) -> tuple[date, datetime, str]:
    """重放排序键：``(move_date, occurred_at, event_id)`` 升序（formula-spec §2.1）。"""
    return (movement.move_date, _normalized_occurred_at(movement), movement.event_id)


@dataclass(frozen=True)
class BucketReplay:
    """单个 ``(sku_id, warehouse_id)`` 桶的重放结果。"""

    sku_id: str
    warehouse_id: str
    #: 期初余额（历史事件重放结果，含盘点替换）
    opening_qty: Decimal
    #: 期末余额（历史 + 本期事件重放结果，含盘点替换；允许为负）
    closing_qty: Decimal
    #: 期间出库量（Σ出库 + Σ报废 − Σ退货，含冲销撤销调整；允许为负）
    period_out_qty: Decimal
    #: 期间 COGS（移动加权平均；SKU 级任一桶期内负库存时由上层置 0）
    period_cogs: Decimal
    #: 期内任一时点（含期初进入时点）余额 < 0
    period_negative: bool
    #: 全重放中首个负余额日期（历史或本期）；None 表示从未为负
    first_negative_date: date | None
    #: 全重放中首个负余额数值
    first_negative_value: Decimal | None


@dataclass(frozen=True)
class ReplayOutcome:
    """重放内核输出：桶级明细、SKU 级单位成本与 Warning。"""

    #: 桶级结果，按 (sku_id, warehouse_id) 字典序排列（确定性）
    buckets: tuple[BucketReplay, ...]
    #: F-KPI-005 单位成本口径：SKU 最近一次携带 unit_cost 的入库类事件成本
    last_cost_by_sku: dict[str, Decimal] = field(default_factory=dict)
    #: 出库时无任何已知成本记录（该笔 COGS 记 0）的 SKU
    cogs_missing_cost_skus: frozenset[str] = frozenset()
    #: 桶级首个负余额 Warning（NEGATIVE_BALANCE，非阻断）
    warnings: tuple[Warning, ...] = ()


class _BucketState:
    """单个 ``(sku_id, warehouse_id)`` 桶的重放状态机。"""

    def __init__(self, sku_id: str, warehouse_id: str) -> None:
        self.sku_id = sku_id
        self.warehouse_id = warehouse_id
        self.balance = Decimal(0)
        self.avg_cost: Decimal | None = None
        self.opening_qty = Decimal(0)
        self._opening_captured = False
        self.period_out_qty = Decimal(0)
        self.period_cogs = Decimal(0)
        self.period_negative = False
        self.first_negative_date: date | None = None
        self.first_negative_value: Decimal | None = None
        self.missing_cost_out = False
        self._pre_stocktake: dict[str, Decimal] = {}

    def capture_opening_if_needed(self) -> None:
        """进入本期前捕获期初余额（幂等）；期初为负视为期内负库存。"""
        if self._opening_captured:
            return
        self.opening_qty = self.balance
        self._opening_captured = True
        if self.balance < 0:
            self.period_negative = True

    def _track_negative(self, move_date: date, in_period: bool) -> None:
        """负库存跟踪：首个负余额（Warning）与期内负余额标记（COGS 置零条件）。"""
        if self.balance >= 0:
            return
        if self.first_negative_date is None:
            self.first_negative_date = move_date
            self.first_negative_value = self.balance
        if in_period:
            self.period_negative = True

    def _update_avg_inbound(self, qty: Decimal, unit_cost: Decimal | None, sign: int) -> None:
        """入库/退货后的移动加权平均更新；sign=-1 为撤销（按剩余余额近似还原）。"""
        if unit_cost is None:
            return
        if sign > 0:
            if self.avg_cost is None:
                # 此前无任何已知成本记录：以本笔成本作为加权平均起点
                self.avg_cost = unit_cost
                return
            denominator = self.balance + qty
            if denominator == 0:
                return  # balance_qty + in_qty = 0：不重算 avg_cost（§3.6 边界 3）
            self.avg_cost = (self.balance * self.avg_cost + qty * unit_cost) / denominator
        else:
            if self.avg_cost is None:
                return
            denominator = self.balance - qty
            if denominator == 0:
                return
            self.avg_cost = (self.balance * self.avg_cost - qty * unit_cost) / denominator

    def apply(self, base: MovementRecord, sign: int, base_in_period: bool) -> None:
        """按基础事件与生效符号推进桶状态。

        - ``base``：冲销链解析出的基础事件（非冲销事件为其自身）；
        - ``sign``：+1 为正向应用，-1 为撤销（冲销层数为奇数）；
        - ``base_in_period``：基础事件自身是否落在分析期内，决定其是否
          贡献本期出库量/COGS（余额影响恒在事件生效时点计入，不受此约束）。
        """
        move_type = base.move_type
        qty = base.quantity
        if move_type in _INBOUND_TYPES:
            self._update_avg_inbound(qty, base.unit_cost, sign)
            self.balance += qty if sign > 0 else -qty
            if base_in_period and move_type is MoveType.RETURN:
                # 退货冲减期间 COGS 与出库量；撤销退货时等额加回
                cost = self.avg_cost if self.avg_cost is not None else Decimal(0)
                self.period_cogs += -qty * cost if sign > 0 else qty * cost
                self.period_out_qty += -qty if sign > 0 else qty
        elif move_type in _OUTBOUND_TYPES:
            self.balance += -qty if sign > 0 else qty
            if base_in_period:
                cost = self.avg_cost if self.avg_cost is not None else Decimal(0)
                # 出库/报废计入 COGS 与出库量；撤销时等额冲回
                self.period_cogs += qty * cost if sign > 0 else -qty * cost
                self.period_out_qty += qty if sign > 0 else -qty
                if sign > 0 and self.avg_cost is None:
                    # 出库时无任何已知成本记录：该笔成本记 0（§3.6 边界 2）
                    self.missing_cost_out = True
        elif move_type is MoveType.STOCKTAKE:
            if sign > 0:
                # 重放至盘点日：余额替换为实盘数量，差额不计净入库/出库量/COGS
                self._pre_stocktake[base.event_id] = self.balance
                self.balance = qty
            else:
                pre = self._pre_stocktake.get(base.event_id)
                if pre is not None:
                    # 撤销盘点 = 扣除当时的替换差额（delta 语义，与中间发生的事件无关；
                    # 盘点尚未生效时撤销为 no-op）
                    self.balance -= qty - pre
        # TRANSFER：单条记录无方向语义，重放 no-op（不影响余额/出库量/COGS）

    def apply_and_track(
        self,
        base: MovementRecord,
        sign: int,
        event_in_period: bool,
        base_in_period: bool,
        move_date: date,
    ) -> None:
        """应用事件并跟踪负库存（日期取事件自身的 move_date）。

        ``event_in_period`` 为当前处理事件（冲销时为冲销事件自身）的期间归属，
        驱动期内负余额标记；``base_in_period`` 见 :meth:`apply`。
        """
        self.apply(base, sign, base_in_period)
        self._track_negative(move_date, event_in_period)

    def to_result(self) -> BucketReplay:
        """导出为不可变的桶级重放结果。"""
        return BucketReplay(
            sku_id=self.sku_id,
            warehouse_id=self.warehouse_id,
            opening_qty=self.opening_qty,
            closing_qty=self.balance,
            period_out_qty=self.period_out_qty,
            period_cogs=self.period_cogs,
            period_negative=self.period_negative,
            first_negative_date=self.first_negative_date,
            first_negative_value=self.first_negative_value,
        )


def _resolve_reversal(
    reversal: MovementRecord,
    events_by_id: dict[str, MovementRecord],
) -> tuple[MovementRecord, int]:
    """解析冲销引用链，返回 ``(基础事件, 生效符号)``。

    符号 = (−1)^链上冲销层数：直接冲销为 −1（撤销），撤销的撤销为 +1（复原）。
    引用缺失或成环时抛 :class:`DataValidationError`（校验层已拦截，此处为
    直接调用重放内核的防御性兜底）。
    """
    reference = reversal.reversal_of
    target = events_by_id.get(reference) if reference else None
    if target is None:
        raise DataValidationError(
            f"冲销事件 {reversal.event_id} 缺少引用或引用了不存在的事件。",
            details=[
                {
                    "field": "movements.reversal_of",
                    "event_id": reversal.event_id,
                    "reversal_of": reference,
                }
            ],
        )
    sign = -1
    visited = {reversal.event_id}
    current = target
    while current.move_type is MoveType.REVERSAL:
        if current.event_id in visited:
            raise DataValidationError(
                f"冲销引用链存在环（经过 {current.event_id}）。",
                details=[
                    {
                        "field": "movements.reversal_of",
                        "event_id": reversal.event_id,
                    }
                ],
            )
        visited.add(current.event_id)
        next_id = current.reversal_of
        next_event = events_by_id.get(next_id) if next_id else None
        if next_event is None:
            raise DataValidationError(
                f"冲销事件 {current.event_id} 缺少引用或引用了不存在的事件。",
                details=[
                    {
                        "field": "movements.reversal_of",
                        "event_id": current.event_id,
                    }
                ],
            )
        sign = -sign
        current = next_event
    return current, sign


def _negative_balance_warning(bucket: BucketReplay) -> Warning:
    """构造桶级 NEGATIVE_BALANCE Warning（fields 定位 SKU、仓库、日期与数值）。"""
    assert bucket.first_negative_date is not None
    assert bucket.first_negative_value is not None
    return Warning(
        code="NEGATIVE_BALANCE",
        severity=WarningSeverity.WARN,
        message=(
            f"SKU {bucket.sku_id} 在仓库 {bucket.warehouse_id} 于 "
            f"{bucket.first_negative_date.isoformat()} 出现负余额 "
            f"{bucket.first_negative_value}，余额照常参与计算，请核对事件完整性。"
        ),
        fields=[
            bucket.sku_id,
            bucket.warehouse_id,
            bucket.first_negative_date.isoformat(),
            str(bucket.first_negative_value),
        ],
        blocking=False,
    )


def replay_movements(
    request: AnalysisRequest,
    movements: Sequence[MovementRecord],
) -> ReplayOutcome:
    """重放全部库存事件，输出桶级期初/期末/出库量/COGS 与负库存信息。

    - 仓库范围：仅重放 ``warehouse_id ∈ request.warehouse_ids`` 的事件
      （``warehouse_ids`` 为空视为不限仓库）；
    - 重放视界：``move_date < end_date`` 的全部事件（历史 + 本期）；
    - 期间归属：``start_date <= move_date < end_date`` 为本期，其余为历史。
    """
    scope = set(request.warehouse_ids)
    events_by_id = {movement.event_id: movement for movement in movements}
    ordered = sorted(
        (
            movement
            for movement in movements
            if movement.move_date < request.end_date
            and (not scope or movement.warehouse_id in scope)
        ),
        key=replay_sort_key,
    )

    buckets: dict[tuple[str, str], _BucketState] = {}
    last_cost_by_sku: dict[str, Decimal] = {}
    missing_cost_skus: set[str] = set()

    for event in ordered:
        event_in_period = event.move_date >= request.start_date
        if event.move_type is MoveType.REVERSAL:
            base, sign = _resolve_reversal(event, events_by_id)
            # 撤销作用于基础事件所属桶（等价于撤销原事件的库存影响）；
            # 基础事件不在重放范围内（未来事件/范围外仓库）时无从撤销，跳过
            base_in_scope = (
                base.move_date < request.end_date
                and (not scope or base.warehouse_id in scope)
            )
            if not base_in_scope:
                continue
            key = (base.sku_id, base.warehouse_id)
            base_in_period = request.start_date <= base.move_date < request.end_date
        else:
            base, sign = event, 1
            key = (event.sku_id, event.warehouse_id)
            base_in_period = event_in_period
        bucket = buckets.setdefault(key, _BucketState(*key))
        if event_in_period:
            bucket.capture_opening_if_needed()
        bucket.apply_and_track(base, sign, event_in_period, base_in_period, event.move_date)
        if bucket.missing_cost_out:
            missing_cost_skus.add(bucket.sku_id)
        if (
            event.move_type in _INBOUND_TYPES
            and event.unit_cost is not None
        ):
            # F-KPI-005 单位成本口径：最近一次携带 unit_cost 的入库类事件
            last_cost_by_sku[event.sku_id] = event.unit_cost

    # 无本期事件的桶：期初即期末余额
    for bucket in buckets.values():
        bucket.capture_opening_if_needed()

    results = tuple(
        buckets[key].to_result() for key in sorted(buckets)
    )
    warnings = tuple(
        _negative_balance_warning(bucket)
        for bucket in results
        if bucket.first_negative_date is not None
    )
    return ReplayOutcome(
        buckets=results,
        last_cost_by_sku=last_cost_by_sku,
        cogs_missing_cost_skus=frozenset(missing_cost_skus),
        warnings=warnings,
    )
