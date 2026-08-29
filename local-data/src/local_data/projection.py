"""库存余额投影重建（M1 Task 3，单仓口径）。

职责与口径说明（重要）：
- ``inventory_event`` 是唯一库存事实来源，``inventory_balance`` 是可整表重建的
  查询投影（§35.4）：重建 = 同一事务内清空余额表后，按 ``occurred_at`` 顺序
  全量回放事件重新写入，不保存任何不可再生状态。
- 回放排序键 ``(occurred_at, event_id)``：同一时刻按 event_id 稳定排序，
  保证任意次重建结果一致（确定性）。
- move_type 语义表（事件数量恒为正，方向由类型承载）：

    ==============  ======  ==========================================
    move_type       方向    口径
    INBOUND         +q      入库
    OUTBOUND        -q      出库
    RETURN          +q      退货入库
    SCRAP           -q      报废
    TRANSFER_IN     +q      调拨入仓（M1 单仓口径：调拨在同表内拆为
    TRANSFER_OUT    -q      出/入两条事件记录，不再建跨仓结构）
    ADJUSTMENT      差额    盘点：quantity 记实盘数，投影按
                            “实盘数 - 当前账面” 取调整量（可加可减），
                            回放序确定故结果确定
    REVERSAL        反转    冲销：按 reversal_of 原事件的数量取反方向；
                            原事件为 ADJUSTMENT 的冲销 M1 不支持
                            （以反向盘点表达），记 Warning 跳过
    ==============  ======  ==========================================

- ``available_qty = on_hand_qty``（M1 无预留概念，``reserved_qty`` 恒 0）。
- 出库导致负库存时不阻断回放（事实来源原则：事件先落库为真），最终为负的
  维度组合记入 ``warnings`` 返回，由上层以告警形式呈现。
- 余额身份维度 ``(sku_id, warehouse_id, location_id, lot_id)``；NULL 维度
  （无库位/批次的事件）单独成组。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from local_data.models import (
    MOVE_ADJUSTMENT,
    MOVE_INBOUND,
    MOVE_OUTBOUND,
    MOVE_RETURN,
    MOVE_REVERSAL,
    MOVE_SCRAP,
    MOVE_TRANSFER_IN,
    MOVE_TRANSFER_OUT,
    InventoryBalanceRow,
    InventoryEventRow,
    utc_now_iso,
)

#: 方向型 move_type 的余额增减符号（+1 入 / -1 出）
_DIRECTION_SIGNS: dict[str, int] = {
    MOVE_INBOUND: 1,
    MOVE_OUTBOUND: -1,
    MOVE_RETURN: 1,
    MOVE_SCRAP: -1,
    MOVE_TRANSFER_IN: 1,
    MOVE_TRANSFER_OUT: -1,
}

#: 余额身份维度组合键：(sku_id, warehouse_id, location_id, lot_id)
_BalanceKey = tuple[str, str, str | None, str | None]


@dataclass(frozen=True)
class ProjectionResult:
    """一次投影重建的结果摘要。"""

    event_count: int  # 回放的事件总数
    balance_count: int  # 写入的余额行数（维度组合数）
    warnings: list[str] = field(default_factory=list)  # 告警（不阻断）


class BalanceProjectionService:
    """inventory_balance 重建服务：清空 → 按 occurred_at 回放 → 全量重写。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def rebuild(self) -> ProjectionResult:
        """整表重建余额投影（幂等：任意次重建结果一致）。"""
        warnings: list[str] = []
        with self._session_factory() as session, session.begin():
            session.execute(delete(InventoryBalanceRow))
            events = list(
                session.execute(
                    select(InventoryEventRow).order_by(
                        InventoryEventRow.occurred_at, InventoryEventRow.event_id
                    )
                ).scalars()
            )
            original_by_id = {event.event_id: event for event in events}
            on_hand: dict[_BalanceKey, Decimal] = {}
            as_of: dict[_BalanceKey, str] = {}
            for event in events:
                key: _BalanceKey = (
                    event.sku_id,
                    event.warehouse_id,
                    event.location_id,
                    event.lot_id,
                )
                current = on_hand.get(key, Decimal(0))
                on_hand[key] = current + self._event_delta(event, original_by_id, current, warnings)
                as_of[key] = event.event_id
            now = utc_now_iso()
            for key in sorted(on_hand, key=_null_first_sort_key):
                quantity = on_hand[key]
                if quantity < 0:
                    # 事实来源原则：负库存照常入库并告警，不阻断重建
                    warnings.append(
                        "NEGATIVE_BALANCE:sku_id="
                        f"{key[0]},warehouse_id={key[1]},location_id={key[2]},"
                        f"lot_id={key[3]},on_hand_qty={quantity}"
                    )
                session.add(
                    InventoryBalanceRow(
                        sku_id=key[0],
                        warehouse_id=key[1],
                        location_id=key[2],
                        lot_id=key[3],
                        on_hand_qty=quantity,
                        available_qty=quantity,  # M1 无预留：可用 = 在手
                        reserved_qty=Decimal(0),
                        as_of_event_id=as_of.get(key),
                        created_at=now,
                        updated_at=now,
                    )
                )
        return ProjectionResult(
            event_count=len(events),
            balance_count=len(on_hand),
            warnings=warnings,
        )

    def list_balances(self) -> list[InventoryBalanceRow]:
        """读取当前余额投影，按维度稳定排序（确定性比对用）。"""
        with self._session_factory() as session:
            return list(
                session.execute(
                    select(InventoryBalanceRow).order_by(
                        InventoryBalanceRow.sku_id,
                        InventoryBalanceRow.warehouse_id,
                        InventoryBalanceRow.location_id,
                        InventoryBalanceRow.lot_id,
                        InventoryBalanceRow.id,
                    )
                ).scalars()
            )

    def _event_delta(
        self,
        event: InventoryEventRow,
        original_by_id: dict[str, InventoryEventRow],
        current_qty: Decimal,
        warnings: list[str],
    ) -> Decimal:
        """单个事件对所在维度组合的余额调整量（口径见模块 docstring）。"""
        if event.move_type == MOVE_ADJUSTMENT:
            # 盘点（实盘数口径）：调整量 = 实盘数 - 当前账面
            return event.quantity - current_qty
        if event.move_type == MOVE_REVERSAL:
            original = (
                original_by_id.get(event.reversal_of) if event.reversal_of else None
            )
            if original is None:
                warnings.append(
                    f"REVERSAL_TARGET_MISSING:event_id={event.event_id},"
                    f"reversal_of={event.reversal_of}"
                )
                return Decimal(0)
            if original.move_type == MOVE_ADJUSTMENT:
                # M1 口径：盘点冲销以反向盘点表达，冲销事件本身记 Warning 跳过
                warnings.append(
                    f"REVERSAL_UNSUPPORTED:event_id={event.event_id},"
                    f"reversal_of={event.reversal_of},target=ADJUSTMENT"
                )
                return Decimal(0)
            sign = _DIRECTION_SIGNS.get(original.move_type)
            if sign is None:
                warnings.append(
                    f"UNKNOWN_MOVE_TYPE:event_id={event.event_id},"
                    f"move_type={original.move_type}"
                )
                return Decimal(0)
            return Decimal(-sign) * original.quantity
        sign = _DIRECTION_SIGNS.get(event.move_type)
        if sign is None:
            warnings.append(
                f"UNKNOWN_MOVE_TYPE:event_id={event.event_id},move_type={event.move_type}"
            )
            return Decimal(0)
        return Decimal(sign) * event.quantity


def _null_first_sort_key(key: _BalanceKey) -> tuple[str, str, tuple[int, str], tuple[int, str]]:
    """维度组合排序键：NULL 维度排前，保证写入顺序确定（SQLite NULL 默认靠前）。"""
    location = (0, "") if key[2] is None else (1, key[2])
    lot = (0, "") if key[3] is None else (1, key[3])
    return (key[0], key[1], location, lot)
