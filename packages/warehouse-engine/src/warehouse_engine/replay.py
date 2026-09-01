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

M2 追加的采集项（供 ABC/库龄/呆滞/补货/预测共用，全部为带默认值的追加字段，
``buckets`` 内容与顺序零变化，M1 行为不变）：

- 逐日需求（``daily_out_by_sku``）：``[start_date, end_date)`` 内每一天的
  ``max(0, 当日出库 + 报废 − 退货)``（§7.1 日需求口径，逐日取下限 0），
  零出库日补 ``Decimal(0)``，按日期升序；事件归属取**事件自身**的 ``move_date``
  且要求事件本身落在本期（与 ``_track_negative`` 的期间判定一致）；
- 日期索引（``last_outbound_date_by_sku`` / ``first_inbound_date_by_sku``）：
  全重放视界（含 ``move_date < start_date`` 的历史）内的末次出库/报废日期与
  首次入库/退货日期。冲销事件本身不更新这两个索引（撤销并非出入库行为）；
- 批次账（``lot_balances``）：有 ``lot_id`` 时按 ``(sku, wh, lot)`` 维护批次
  余额，出库/报废按 FIFO 消耗最早切片；同一批次再次入库时 ``last_inbound_date``
  取最近一次入库日期（§5 批次口径字面语义）；
- 残余入库（``residual_inbound_by_sku``）：无 ``lot_id`` 时 FIFO 消耗后剩余的
  入库切片，即"构成期末余额的入库事件"（§5 平均库龄分子总体）；
- 全历史入库（``all_inbound_by_sku``）：以 **O(1) 内存的加权聚合**代替保存
  全量事件——保存 ``Σqty`` 与 ``Σ(qty × move_date.toordinal())``，观察点 E 下的
  平均库龄由 ``E×Σqty − Σ(qty×ordinal)`` 除以 ``Σqty`` 精确换算得到；
- 构成不足的判定（盘点/负库存/冲销撤销导致残余与期末余额不一致）由上层
  ``abc_aging`` 完成并回退到全历史口径，本模块只负责提供两套口径的数据。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
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

#: 逐日序列的日期步进（避免逐日构造 timedelta 的重复开销）
_ONE_DAY = timedelta(days=1)


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
class InboundLot:
    """入库切片：构成（或曾构成）期末余额的一笔入库，用于 §5 平均库龄。"""

    #: 入库日期（库龄 = 观察点 − 该日期）
    move_date: date
    #: 数量（残余模式下为 FIFO 消耗后的剩余量；全历史模式下为原始入库量）
    qty: Decimal
    #: 该笔入库的单位成本（库龄金额分布用；缺失为 None）
    unit_cost: Decimal | None


@dataclass(frozen=True)
class LotBalance:
    """单个 ``(sku_id, warehouse_id, lot_id)`` 桶的期末批次余额（§5 批次口径）。"""

    sku_id: str
    warehouse_id: str
    lot_id: str
    #: 期末该批次余额（FIFO 消耗后；可为 0）
    qty: Decimal
    #: 该批次最近一次入库日期（库龄 = 观察点 − 该日期）
    last_inbound_date: date


@dataclass(frozen=True)
class InboundAggregate:
    """全历史入库的加权聚合（O(1) 内存，供"构成不足"时回退）。

    观察点 ``E`` 下的平均库龄由下式精确换算（``ordinal`` 为 ``date.toordinal()``）：
    ``avg_age = (E.toordinal() × total_qty − weighted_ordinal) / total_qty``。
    """

    #: 入库数量合计
    total_qty: Decimal
    #: ``Σ(qty_i × move_date_i.toordinal())``
    weighted_ordinal: Decimal
    #: 参与聚合的入库事件条数（样本量标注用）
    event_count: int


@dataclass(frozen=True)
class ReplayOutcome:
    """重放内核输出：桶级明细、SKU 级单位成本与 Warning（M2 追加采集项）。"""

    #: 桶级结果，按 (sku_id, warehouse_id) 字典序排列（确定性）
    buckets: tuple[BucketReplay, ...]
    #: F-KPI-005 单位成本口径：SKU 最近一次携带 unit_cost 的入库类事件成本
    last_cost_by_sku: dict[str, Decimal] = field(default_factory=dict)
    #: 出库时无任何已知成本记录（该笔 COGS 记 0）的 SKU
    cogs_missing_cost_skus: frozenset[str] = frozenset()
    #: 桶级首个负余额 Warning（NEGATIVE_BALANCE，非阻断）
    warnings: tuple[Warning, ...] = ()
    # --- 以下为 M2 追加采集项（均有默认值，M1 构造方式不受影响）---
    #: SKU 级逐日需求：``[start_date, end_date)`` 内每一天一项，按日期升序，
    #: 值为 ``max(0, 当日出库 + 报废 − 退货)``（§7.1 逐日取下限 0）
    daily_out_by_sku: dict[str, tuple[tuple[date, Decimal], ...]] = field(default_factory=dict)
    #: SKU 级末次出库/报废日期（全重放视界）；无出库记录的 SKU 不在字典中
    last_outbound_date_by_sku: dict[str, date] = field(default_factory=dict)
    #: SKU 级首次入库/退货日期（全重放视界）；无入库记录的 SKU 不在字典中
    first_inbound_date_by_sku: dict[str, date] = field(default_factory=dict)
    #: 期末批次余额，按 (sku_id, warehouse_id, lot_id) 字典序（确定性）
    lot_balances: tuple[LotBalance, ...] = ()
    #: 无 lot_id：FIFO 消耗后构成期末余额的入库切片（§5 平均库龄分子总体）
    residual_inbound_by_sku: dict[str, tuple[InboundLot, ...]] = field(default_factory=dict)
    #: 无 lot_id：全历史入库加权聚合（"构成不足"时的回退总体）
    all_inbound_by_sku: dict[str, InboundAggregate] = field(default_factory=dict)


@dataclass
class _LotSlice:
    """批次账中的一条入库切片（可变：出库时按 FIFO 扣减 ``qty``）。"""

    lot_id: str | None
    move_date: date
    qty: Decimal
    unit_cost: Decimal | None


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
        # --- M2 采集项 ---
        #: 本期逐日出库量增量（未取 max(0, ·)，导出时逐日钳制）
        self.daily_out: dict[date, Decimal] = {}
        #: 批次账（FIFO 顺序：先入的在列表前部）
        self.lot_slices: list[_LotSlice] = []
        #: 末次出库/报废日期（全重放视界）
        self.last_outbound_date: date | None = None
        #: 首次入库/退货日期（全重放视界）
        self.first_inbound_date: date | None = None
        #: 全历史入库加权聚合（O(1) 内存）
        self.all_inbound_qty = Decimal(0)
        self.all_inbound_weighted_ordinal = Decimal(0)
        self.all_inbound_count = 0

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

    @staticmethod
    def _drain(slices: list[_LotSlice], remaining: Decimal) -> Decimal:
        """按列表顺序从批次切片扣减数量，返回仍未扣完的余量。"""
        for lot_slice in slices:
            if remaining <= 0:
                break
            if lot_slice.qty <= 0:
                continue
            take = min(lot_slice.qty, remaining)
            lot_slice.qty -= take
            remaining -= take
        return remaining

    def _add_lot_slice(
        self, lot_id: str | None, move_date: date, qty: Decimal, unit_cost: Decimal | None
    ) -> None:
        """入库：同批次合并（并取最近入库日期），无批次时按事件独立成片。"""
        if lot_id is not None:
            for lot_slice in self.lot_slices:
                if lot_slice.lot_id == lot_id:
                    lot_slice.qty += qty
                    # 同批次再次入库：last_inbound_date 取最近一次（§5 批次口径）
                    lot_slice.move_date = max(move_date, lot_slice.move_date)
                    return
        self.lot_slices.append(
            _LotSlice(lot_id=lot_id, move_date=move_date, qty=qty, unit_cost=unit_cost)
        )

    def _consume_lot_slice(self, qty: Decimal, lot_id: str | None) -> None:
        """出库/报废：优先扣同一 lot_id 的切片，不足部分按 FIFO 扣其余切片。"""
        remaining = qty
        if lot_id is not None:
            remaining = self._drain(
                [s for s in self.lot_slices if s.lot_id == lot_id], remaining
            )
        self._drain(list(self.lot_slices), remaining)

    def _remove_lot_slice(self, qty: Decimal, lot_id: str | None) -> None:
        """撤销入库：按逆序回扣最近加入的切片（尽力而为，不足部分忽略）。"""
        remaining = qty
        candidates = (
            [s for s in self.lot_slices if s.lot_id == lot_id]
            if lot_id is not None
            else list(self.lot_slices)
        )
        for lot_slice in reversed(candidates):
            if remaining <= 0:
                break
            take = min(lot_slice.qty, remaining)
            lot_slice.qty -= take
            remaining -= take

    def _track_all_inbound(self, qty: Decimal, move_date: date, sign: int) -> None:
        """全历史入库加权聚合（O(1) 内存）：Σqty 与 Σ(qty × ordinal)。"""
        weighted = qty * Decimal(move_date.toordinal())
        if sign > 0:
            self.all_inbound_qty += qty
            self.all_inbound_weighted_ordinal += weighted
            self.all_inbound_count += 1
        else:
            self.all_inbound_qty -= qty
            self.all_inbound_weighted_ordinal -= weighted
            self.all_inbound_count = max(0, self.all_inbound_count - 1)

    def apply(self, base: MovementRecord, sign: int, base_in_period: bool) -> Decimal:
        """按基础事件与生效符号推进桶状态，返回本次对期间出库量的增量。

        - ``base``：冲销链解析出的基础事件（非冲销事件为其自身）；
        - ``sign``：+1 为正向应用，-1 为撤销（冲销层数为奇数）；
        - ``base_in_period``：基础事件自身是否落在分析期内，决定其是否
          贡献本期出库量/COGS（余额影响恒在事件生效时点计入，不受此约束）；
        - 返回值：本次对 ``period_out_qty`` 的增量（已按 ``base_in_period``
          门控），供 ``daily_out_by_sku`` 复用以保证两者口径恒等。
        """
        move_type = base.move_type
        qty = base.quantity
        out_delta = Decimal(0)
        if move_type in _INBOUND_TYPES:
            self._update_avg_inbound(qty, base.unit_cost, sign)
            self.balance += qty if sign > 0 else -qty
            self._track_all_inbound(qty, base.move_date, sign)
            if sign > 0:
                self._add_lot_slice(base.lot_id, base.move_date, qty, base.unit_cost)
                if self.first_inbound_date is None or base.move_date < self.first_inbound_date:
                    self.first_inbound_date = base.move_date
            else:
                self._remove_lot_slice(qty, base.lot_id)
            if base_in_period and move_type is MoveType.RETURN:
                # 退货冲减期间 COGS 与出库量；撤销退货时等额加回
                cost = self.avg_cost if self.avg_cost is not None else Decimal(0)
                self.period_cogs += -qty * cost if sign > 0 else qty * cost
                out_delta = -qty if sign > 0 else qty
                self.period_out_qty += out_delta
        elif move_type in _OUTBOUND_TYPES:
            self.balance += -qty if sign > 0 else qty
            if sign > 0:
                self._consume_lot_slice(qty, base.lot_id)
                if self.last_outbound_date is None or base.move_date > self.last_outbound_date:
                    self.last_outbound_date = base.move_date
            if base_in_period:
                cost = self.avg_cost if self.avg_cost is not None else Decimal(0)
                # 出库/报废计入 COGS 与出库量；撤销时等额冲回
                self.period_cogs += qty * cost if sign > 0 else -qty * cost
                out_delta = qty if sign > 0 else -qty
                self.period_out_qty += out_delta
                if sign > 0 and self.avg_cost is None:
                    # 出库时无任何已知成本记录：该笔成本记 0（§3.6 边界 2）
                    self.missing_cost_out = True
        elif move_type is MoveType.STOCKTAKE:
            if sign > 0:
                # 重放至盘点日：余额替换为实盘数量，差额不计净入库/出库量/COGS
                self._pre_stocktake[base.event_id] = self.balance
                self.balance = qty
                # 盘点后批次构成不可知：清空批次账（不编造库龄），由上层按
                # "构成不足"回退到全历史口径（§5 边界规则）
                self.lot_slices.clear()
            else:
                pre = self._pre_stocktake.get(base.event_id)
                if pre is not None:
                    # 撤销盘点 = 扣除当时的替换差额（delta 语义，与中间发生的事件无关；
                    # 盘点尚未生效时撤销为 no-op）
                    self.balance -= qty - pre
        # TRANSFER：单条记录无方向语义，重放 no-op（不影响余额/出库量/COGS）
        return out_delta

    def apply_and_track(
        self,
        base: MovementRecord,
        sign: int,
        event_in_period: bool,
        base_in_period: bool,
        move_date: date,
    ) -> None:
        """应用事件、跟踪负库存并累计逐日出库增量。

        ``event_in_period`` 为当前处理事件（冲销时为冲销事件自身）的期间归属，
        驱动期内负余额标记；``base_in_period`` 见 :meth:`apply`。逐日增量的
        期间门控与 ``period_out_qty`` 一致（均为 ``base_in_period``），保证
        ``Σ daily_out == period_out_qty`` 恒成立；日期归属取事件自身的
        ``move_date``（与负库存跟踪一致），超出期间的键由导出阶段裁剪。
        """
        out_delta = self.apply(base, sign, base_in_period)
        self._track_negative(move_date, event_in_period)
        if out_delta != 0:
            self.daily_out[move_date] = self.daily_out.get(move_date, Decimal(0)) + out_delta

    def lot_balances(self) -> tuple[LotBalance, ...]:
        """导出有 ``lot_id`` 的期末批次余额（FIFO 消耗后）。"""
        return tuple(
            LotBalance(
                sku_id=self.sku_id,
                warehouse_id=self.warehouse_id,
                lot_id=str(lot_slice.lot_id),
                qty=lot_slice.qty,
                last_inbound_date=lot_slice.move_date,
            )
            for lot_slice in sorted(
                (s for s in self.lot_slices if s.lot_id is not None),
                key=lambda s: str(s.lot_id),
            )
        )

    def residual_inbound(self) -> tuple[InboundLot, ...]:
        """导出 FIFO 消耗后剩余的入库切片（§5 平均库龄分子总体）。

        仅导出无 ``lot_id`` 的切片：带批次的数据走 :meth:`lot_balances` 口径。
        """
        return tuple(
            InboundLot(move_date=s.move_date, qty=s.qty, unit_cost=s.unit_cost)
            for s in sorted(
                (s for s in self.lot_slices if s.lot_id is None and s.qty > 0),
                key=lambda s: (s.move_date, str(s.qty)),
            )
        )

    def inbound_aggregate(self) -> InboundAggregate:
        """导出全历史入库加权聚合（O(1) 内存口径）。"""
        return InboundAggregate(
            total_qty=self.all_inbound_qty,
            weighted_ordinal=self.all_inbound_weighted_ordinal,
            event_count=self.all_inbound_count,
        )

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
    """重放全部库存事件，输出桶级结果与 M1/M2 共用的派生采集项。

    - 仓库范围：仅重放 ``warehouse_id ∈ request.warehouse_ids`` 的事件
      （``warehouse_ids`` 为空视为不限仓库）；
    - 重放视界：``move_date < end_date`` 的全部事件（历史 + 本期）；
    - 期间归属：``start_date <= move_date < end_date`` 为本期，其余为历史；
    - M2 采集项在桶级采集后按 SKU 归并：逐日需求为各仓库分桶之和并按期间
      补齐零出库日；末次出库/首次入库取各桶的极值；批次余额按
      ``(sku_id, warehouse_id, lot_id)`` 字典序输出；入库切片与全历史聚合
      跨仓库合并为 SKU 级总体。
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

    # --- M2 采集项：桶级 → SKU 级归并 ---
    daily_totals: dict[str, dict[date, Decimal]] = {}
    last_outbound_date_by_sku: dict[str, date] = {}
    first_inbound_date_by_sku: dict[str, date] = {}
    lot_balances: list[LotBalance] = []
    residual_by_sku: dict[str, list[InboundLot]] = {}
    all_inbound_qty: dict[str, Decimal] = {}
    all_inbound_ordinal: dict[str, Decimal] = {}
    all_inbound_count: dict[str, int] = {}
    for key in sorted(buckets):
        bucket = buckets[key]
        sku_id = bucket.sku_id
        daily = daily_totals.setdefault(sku_id, {})
        for day, delta in bucket.daily_out.items():
            if request.start_date <= day < request.end_date:
                daily[day] = daily.get(day, Decimal(0)) + delta
        if bucket.last_outbound_date is not None:
            current = last_outbound_date_by_sku.get(sku_id)
            if current is None or bucket.last_outbound_date > current:
                last_outbound_date_by_sku[sku_id] = bucket.last_outbound_date
        if bucket.first_inbound_date is not None:
            current = first_inbound_date_by_sku.get(sku_id)
            if current is None or bucket.first_inbound_date < current:
                first_inbound_date_by_sku[sku_id] = bucket.first_inbound_date
        lot_balances.extend(bucket.lot_balances())
        residual_by_sku.setdefault(sku_id, []).extend(bucket.residual_inbound())
        aggregate = bucket.inbound_aggregate()
        all_inbound_qty[sku_id] = all_inbound_qty.get(sku_id, Decimal(0)) + aggregate.total_qty
        all_inbound_ordinal[sku_id] = (
            all_inbound_ordinal.get(sku_id, Decimal(0)) + aggregate.weighted_ordinal
        )
        all_inbound_count[sku_id] = (
            all_inbound_count.get(sku_id, 0) + aggregate.event_count
        )

    # 逐日需求：补齐期间内零出库日并按日期升序输出（§7.1 逐日取 max(0, ·)）
    daily_out_by_sku: dict[str, tuple[tuple[date, Decimal], ...]] = {}
    for sku_id in sorted(daily_totals):
        daily = daily_totals[sku_id]
        day_count = (request.end_date - request.start_date).days
        daily_out_by_sku[sku_id] = tuple(
            (
                request.start_date + _ONE_DAY * offset,
                max(Decimal(0), daily.get(request.start_date + _ONE_DAY * offset, Decimal(0))),
            )
            for offset in range(max(0, day_count))
        )

    return ReplayOutcome(
        buckets=results,
        last_cost_by_sku=last_cost_by_sku,
        cogs_missing_cost_skus=frozenset(missing_cost_skus),
        warnings=warnings,
        daily_out_by_sku=daily_out_by_sku,
        last_outbound_date_by_sku=last_outbound_date_by_sku,
        first_inbound_date_by_sku=first_inbound_date_by_sku,
        lot_balances=tuple(lot_balances),
        residual_inbound_by_sku={
            sku_id: tuple(sorted(entries, key=lambda lot: (lot.move_date, str(lot.qty))))
            for sku_id, entries in sorted(residual_by_sku.items())
        },
        all_inbound_by_sku={
            sku_id: InboundAggregate(
                total_qty=all_inbound_qty[sku_id],
                weighted_ordinal=all_inbound_ordinal[sku_id],
                event_count=all_inbound_count[sku_id],
            )
            for sku_id in sorted(all_inbound_qty)
        },
    )
