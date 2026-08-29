"""本地库 → ``contracts.EngineDataset`` 适配器（M1 Task 7.1）。

从本地 SQLite 的 sku / inventory_event / stock_snapshot 表构造引擎数据集，
满足 domain 的 ``DatasetProvider`` 端口（SQL 唯一入口在 local-data 模型层，
本类只做读取与契约映射）：

- 金额/数量：local-data 以 DecimalText（TEXT 存 Decimal 序列化字符串）落库，
  ORM 读出即 Decimal 真值，全程无 float；
- 期间过滤：``start_date <= occurred_at 日期 <= end_date``（闭区间），
  快照同样按 ``snapshot_date`` 闭区间过滤；
- 仓库范围：``request.warehouse_ids`` 非空时事件与快照仅保留范围内仓库，
  空列表表示不过滤（全仓）；
- move_type 映射（本地库取值含 M1 单仓口径扩展，契约 ``MoveType`` 无方向扩展）：

  - ``TRANSFER_IN`` / ``TRANSFER_OUT`` → ``TRANSFER``：本地库把调拨拆为
    出/入两条事件（单仓口径同表简化），契约只有无方向的 TRANSFER；
  - ``ADJUSTMENT`` → ``STOCKTAKE``：本地库盘点语义为“quantity 记实盘数、
    按差额调整”，契约 STOCKTAKE 同为盘点口径；
  - 其余（INBOUND/OUTBOUND/RETURN/SCRAP/REVERSAL）与契约一一对应。

- source 映射（本地库自由文本 → 契约 ``EventSource``）：
  ``IMPORT*``（含 IMPORT_CSV）→ IMPORT、``UI``/``DESKTOP`` → DESKTOP、
  ``MINI_PROGRAM`` → MINI_PROGRAM、``ADJUSTMENT`` → ADJUSTMENT；
  未知取值回退 IMPORT（本地数据以导入来源为主，不因未知值中断构造）；
- replenishment 恒为空列表：本地库 supplier_sku 无 avg_daily_demand 字段，
  补货参数随 B 侧 replenishment 里程碑落地（spec 明确不做）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from contracts import (
    AnalysisRequest,
    EngineDataset,
    EventSource,
    MovementRecord,
    MoveType,
    SkuRecord,
    SnapshotRecord,
)
from local_data.models import (
    MOVE_ADJUSTMENT,
    MOVE_INBOUND,
    MOVE_OUTBOUND,
    MOVE_RETURN,
    MOVE_REVERSAL,
    MOVE_SCRAP,
    MOVE_TRANSFER_IN,
    MOVE_TRANSFER_OUT,
    InventoryEventRow,
    SkuRow,
    StockSnapshotRow,
    WarehouseRow,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

#: 默认请求的 run_id 占位（“一次运行”的新 run_id 由分析用例统一覆盖）
_PLACEHOLDER_RUN_ID = "run-local"

#: 本地库 move_type → 契约 ``MoveType`` 映射（口径说明见模块 docstring）；
#: 迁移 CHECK 保证 move_type 只能取本地库合法值，映射不会缺项。
_MOVE_TYPE_MAP: dict[str, MoveType] = {
    MOVE_INBOUND: MoveType.INBOUND,
    MOVE_OUTBOUND: MoveType.OUTBOUND,
    MOVE_RETURN: MoveType.RETURN,
    MOVE_SCRAP: MoveType.SCRAP,
    MOVE_TRANSFER_IN: MoveType.TRANSFER,
    MOVE_TRANSFER_OUT: MoveType.TRANSFER,
    MOVE_ADJUSTMENT: MoveType.STOCKTAKE,
    MOVE_REVERSAL: MoveType.REVERSAL,
}

#: 本地库 source（自由文本）→ 契约 ``EventSource`` 映射（口径见模块 docstring）
_SOURCE_MAP: dict[str, EventSource] = {
    "IMPORT": EventSource.IMPORT,
    "IMPORT_CSV": EventSource.IMPORT,
    "UI": EventSource.DESKTOP,
    "DESKTOP": EventSource.DESKTOP,
    "MINI_PROGRAM": EventSource.MINI_PROGRAM,
    "ADJUSTMENT": EventSource.ADJUSTMENT,
}


def _parse_occurred_at(text: str) -> datetime:
    """解析库内 UTC ISO 8601 文本；缺失时区信息时按 UTC 补齐。"""
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


class _SnapshotAggregate:
    """同 (sku, 日期, 仓库) 的快照聚合桶。

    stock_snapshot 的唯一维度含库位/批次，契约 ``SnapshotRecord`` 无此维度，
    故同 (sku, 日期, 仓库) 的多行快照求和为时点总量；inventory_value 仅
    聚合非空值（全部为空时保持 None）。
    """

    __slots__ = ("has_value", "quantity", "value")

    def __init__(self) -> None:
        self.quantity = Decimal(0)
        self.value = Decimal(0)
        self.has_value = False

    def add(self, quantity: Decimal, inventory_value: Decimal | None) -> None:
        self.quantity += quantity
        if inventory_value is not None:
            self.value += inventory_value
            self.has_value = True


class SqliteDatasetAdapter:
    """本地库数据集适配器：从本地库读取并构造 ``contracts.EngineDataset``。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def load(self) -> tuple[AnalysisRequest, EngineDataset]:
        """构造默认分析请求（期间覆盖库内全部事件/快照日期）并加载数据集。

        空库时返回各列表为空的数据集与当日单日期间，不抛异常；
        由分析用例负责识别“无数据”并给出明确结果。
        """
        request = self._default_request()
        return request, self.build(request)

    def build(self, request: AnalysisRequest) -> EngineDataset:
        """按请求期间与仓库范围，从本地库构造 ``EngineDataset``。

        - SKU 不按 is_active 过滤：期间内事件仍可能引用已停用 SKU，
          保持引用完整以免引擎校验误报 SKU_NOT_FOUND；
        - 事件排序 (occurred_at, event_id)，与余额投影回放顺序一致（确定性）。
        """
        warehouses = set(request.warehouse_ids)
        with self._session_factory() as session:
            sku_rows = list(
                session.execute(select(SkuRow).order_by(SkuRow.sku_id)).scalars()
            )
            event_rows = list(
                session.execute(
                    select(InventoryEventRow).order_by(
                        InventoryEventRow.occurred_at, InventoryEventRow.event_id
                    )
                ).scalars()
            )
            snapshot_rows = list(
                session.execute(
                    select(StockSnapshotRow).order_by(
                        StockSnapshotRow.snapshot_date,
                        StockSnapshotRow.sku_id,
                        StockSnapshotRow.warehouse_id,
                        StockSnapshotRow.id,
                    )
                ).scalars()
            )

        skus = [
            SkuRecord(
                sku_id=row.sku_id,
                name=row.name,
                category=row.category or "",
                unit=row.unit or "",
                unit_cost=row.unit_cost,
                currency="CNY",
            )
            for row in sku_rows
        ]

        movements: list[MovementRecord] = []
        for row in event_rows:
            occurred = _parse_occurred_at(row.occurred_at)
            if not request.start_date <= occurred.date() <= request.end_date:
                continue
            if warehouses and row.warehouse_id not in warehouses:
                continue
            movements.append(
                MovementRecord(
                    event_id=row.event_id,
                    sku_id=row.sku_id,
                    move_type=_MOVE_TYPE_MAP[row.move_type],
                    quantity=row.quantity,
                    move_date=occurred.date(),
                    occurred_at=occurred,
                    warehouse_id=row.warehouse_id,
                    unit_cost=row.unit_cost,
                    lot_id=row.lot_id,
                    source=_SOURCE_MAP.get(row.source, EventSource.IMPORT),
                )
            )

        buckets: dict[tuple[str, str, str], _SnapshotAggregate] = {}
        for row in snapshot_rows:
            snapshot_day = date.fromisoformat(row.snapshot_date)
            if not request.start_date <= snapshot_day <= request.end_date:
                continue
            if warehouses and row.warehouse_id not in warehouses:
                continue
            key = (row.sku_id, row.snapshot_date, row.warehouse_id)
            buckets.setdefault(key, _SnapshotAggregate()).add(
                row.quantity, row.inventory_value
            )

        snapshots = [
            SnapshotRecord(
                sku_id=sku_id,
                snapshot_date=date.fromisoformat(day),
                quantity=bucket.quantity,
                warehouse_id=warehouse_id,
                inventory_value=bucket.value if bucket.has_value else None,
            )
            for (sku_id, day, warehouse_id), bucket in sorted(buckets.items())
        ]

        return EngineDataset(skus=skus, movements=movements, snapshots=snapshots)

    def _default_request(self) -> AnalysisRequest:
        """默认分析请求：期间覆盖库内全部事件/快照日期，仓库取全部有效仓。"""
        with self._session_factory() as session:
            first_day = session.scalar(select(func.min(InventoryEventRow.occurred_at)))
            last_day = session.scalar(select(func.max(InventoryEventRow.occurred_at)))
            first_snapshot_day = session.scalar(
                select(func.min(StockSnapshotRow.snapshot_date))
            )
            last_snapshot_day = session.scalar(
                select(func.max(StockSnapshotRow.snapshot_date))
            )
            warehouse_ids = list(
                session.execute(
                    select(WarehouseRow.warehouse_id)
                    .where(WarehouseRow.is_active.is_(True))
                    .order_by(WarehouseRow.warehouse_id)
                ).scalars()
            )
        days = [
            text[:10]
            for text in (
                first_day,
                last_day,
                first_snapshot_day,
                last_snapshot_day,
            )
            if text is not None
        ]
        if days:
            start, end = min(days), max(days)
        else:
            # 空库：当日单日期间（数据集为空，由用例返回“无数据”结果）
            start = end = datetime.now(UTC).date().isoformat()
        return AnalysisRequest(
            run_id=_PLACEHOLDER_RUN_ID,
            start_date=date.fromisoformat(start),
            end_date=date.fromisoformat(end),
            warehouse_ids=warehouse_ids,
        )
