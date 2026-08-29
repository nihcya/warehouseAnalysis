"""SqliteDatasetAdapter 测试（M1 Task 7.1，纯逻辑无 Qt）。

覆盖：本地库样本数据构造 ``EngineDataset`` 的字段值正确性——
Decimal 真值、期间闭区间过滤、warehouse_ids 仓库范围过滤、
move_type / source 映射、快照聚合、默认请求推导与空库行为。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from contracts import AnalysisRequest, EventSource, MoveType
from local_data.repository import (
    InventoryEventCreate,
    InventoryEventRepository,
    MasterDataRepository,
    StockSnapshotRepository,
)
from sqlalchemy.orm import Session, sessionmaker
from workbench.infrastructure.db.dataset_adapter import SqliteDatasetAdapter


def _seed_local_data(session_factory: sessionmaker[Session]) -> None:
    """写入两仓两 SKU 样本：含调拨/盘点/冲销事件、批次、期间外事件与快照。"""
    master = MasterDataRepository(session_factory)
    master.add_warehouse(warehouse_id="WH-01", name="主仓")
    master.add_warehouse(warehouse_id="WH-02", name="分仓")
    master.add_sku(
        sku_id="SKU-0001",
        name="矿泉水 550ml",
        category="饮料",
        unit="瓶",
        unit_cost=Decimal("1.20"),
    )
    master.add_sku(
        sku_id="SKU-0002",
        name="能量饮料 250ml",
        category="饮料",
        unit="罐",
        unit_cost=Decimal("3.50"),
    )
    master.add_lot(sku_id="SKU-0001", lot_id="LOT-0001")

    InventoryEventRepository(session_factory).upsert_events(
        [
            InventoryEventCreate(
                event_id="EVT-0001",
                sku_id="SKU-0001",
                warehouse_id="WH-01",
                move_type="INBOUND",
                quantity=Decimal(100),
                occurred_at="2026-06-02T09:00:00+00:00",
                source="IMPORT_CSV",
                unit_cost=Decimal("1.20"),
                lot_id="LOT-0001",
            ),
            InventoryEventCreate(
                event_id="EVT-0002",
                sku_id="SKU-0001",
                warehouse_id="WH-01",
                move_type="TRANSFER_OUT",
                quantity=Decimal(10),
                occurred_at="2026-06-05T10:15:00+00:00",
                source="UI",
            ),
            InventoryEventCreate(
                event_id="EVT-0003",
                sku_id="SKU-0002",
                warehouse_id="WH-02",
                move_type="TRANSFER_IN",
                quantity=Decimal(10),
                occurred_at="2026-06-12T09:40:00+00:00",
                source="UI",
            ),
            InventoryEventCreate(
                event_id="EVT-0004",
                sku_id="SKU-0001",
                warehouse_id="WH-01",
                move_type="ADJUSTMENT",
                quantity=Decimal(85),
                occurred_at="2026-06-30T18:00:00+00:00",
                source="ADJUSTMENT",
            ),
            InventoryEventCreate(
                event_id="EVT-0005",  # 期间外（2026-05-15）
                sku_id="SKU-0002",
                warehouse_id="WH-01",
                move_type="INBOUND",
                quantity=Decimal(50),
                occurred_at="2026-05-15T08:00:00+00:00",
                source="IMPORT_CSV",
            ),
            InventoryEventCreate(
                event_id="EVT-0006",  # 期间边界：end_date 当日（闭区间应保留）
                sku_id="SKU-0002",
                warehouse_id="WH-02",
                move_type="REVERSAL",
                quantity=Decimal(5),
                occurred_at="2026-06-30T23:59:59+00:00",
                source="MINI_PROGRAM",
            ),
        ]
    )

    snapshots = StockSnapshotRepository(session_factory)
    snapshots.save_snapshot(
        snapshot_id="SNAP-0001",
        snapshot_date="2026-06-30",
        sku_id="SKU-0001",
        warehouse_id="WH-01",
        quantity=Decimal(90),
        inventory_value=Decimal("108.00"),
    )
    snapshots.save_snapshot(
        snapshot_id="SNAP-0002",  # 期间外（2026-05-31）
        snapshot_date="2026-05-31",
        sku_id="SKU-0002",
        warehouse_id="WH-02",
        quantity=Decimal(40),
        inventory_value=Decimal("140.00"),
    )


def _request(start: date, end: date, warehouse_ids: list[str]) -> AnalysisRequest:
    return AnalysisRequest(
        run_id="run-adapter-test",
        start_date=start,
        end_date=end,
        warehouse_ids=warehouse_ids,
    )


def test_build_maps_local_rows_to_dataset_fields(
    session_factory: sessionmaker[Session],
) -> None:
    """字段值 100% 对齐契约：Decimal 真值、move_type / source 映射正确。"""
    _seed_local_data(session_factory)
    dataset = SqliteDatasetAdapter(session_factory).build(
        _request(date(2026, 6, 1), date(2026, 6, 30), ["WH-01", "WH-02"])
    )

    # SKU：全量读取（不按 is_active 过滤），金额为 Decimal 真值
    assert [sku.sku_id for sku in dataset.skus] == ["SKU-0001", "SKU-0002"]
    first_sku = dataset.skus[0]
    assert first_sku.name == "矿泉水 550ml"
    assert first_sku.category == "饮料"
    assert first_sku.unit == "瓶"
    assert isinstance(first_sku.unit_cost, Decimal)
    assert first_sku.unit_cost == Decimal("1.20")
    assert first_sku.currency == "CNY"

    # 事件：期间闭区间过滤（05-15 排除、06-30 边界保留），按 (occurred_at, event_id) 排序
    assert [m.event_id for m in dataset.movements] == [
        "EVT-0001",
        "EVT-0002",
        "EVT-0003",
        "EVT-0004",
        "EVT-0006",
    ]
    first = dataset.movements[0]
    assert isinstance(first.quantity, Decimal) and first.quantity == Decimal(100)
    assert first.move_type is MoveType.INBOUND
    assert first.source is EventSource.IMPORT  # IMPORT_CSV → IMPORT
    assert first.move_date == date(2026, 6, 2)
    assert first.occurred_at.isoformat() == "2026-06-02T09:00:00+00:00"
    assert first.unit_cost == Decimal("1.20")
    assert first.lot_id == "LOT-0001"
    assert first.warehouse_id == "WH-01"

    # move_type 映射：TRANSFER_OUT / TRANSFER_IN → TRANSFER；ADJUSTMENT → STOCKTAKE
    assert dataset.movements[1].move_type is MoveType.TRANSFER
    assert dataset.movements[2].move_type is MoveType.TRANSFER
    assert dataset.movements[3].move_type is MoveType.STOCKTAKE
    assert dataset.movements[4].move_type is MoveType.REVERSAL

    # source 映射：UI → DESKTOP；ADJUSTMENT → ADJUSTMENT；MINI_PROGRAM → MINI_PROGRAM
    assert dataset.movements[1].source is EventSource.DESKTOP
    assert dataset.movements[3].source is EventSource.ADJUSTMENT
    assert dataset.movements[4].source is EventSource.MINI_PROGRAM

    # 快照：期间内一条、Decimal 真值；期间外被过滤
    assert len(dataset.snapshots) == 1
    snapshot = dataset.snapshots[0]
    assert snapshot.sku_id == "SKU-0001"
    assert snapshot.snapshot_date == date(2026, 6, 30)
    assert isinstance(snapshot.quantity, Decimal) and snapshot.quantity == Decimal(90)
    assert snapshot.inventory_value == Decimal("108.00")
    assert snapshot.warehouse_id == "WH-01"

    # replenishment：本地库无 avg_daily_demand，M1 恒为空
    assert dataset.replenishment == []


def test_build_filters_by_period_and_warehouse_scope(
    session_factory: sessionmaker[Session],
) -> None:
    """期间 [06-01, 06-10] + 仅 WH-01：事件与快照双重过滤，SKU 主数据保持全量。"""
    _seed_local_data(session_factory)
    dataset = SqliteDatasetAdapter(session_factory).build(
        _request(date(2026, 6, 1), date(2026, 6, 10), ["WH-01"])
    )

    assert [m.event_id for m in dataset.movements] == ["EVT-0001", "EVT-0002"]
    assert all(m.warehouse_id == "WH-01" for m in dataset.movements)
    assert dataset.snapshots == []  # 唯一期间内快照在 06-30
    # SKU 主数据不受期间/仓库过滤（保持引用完整，避免误报 SKU_NOT_FOUND）
    assert [s.sku_id for s in dataset.skus] == ["SKU-0001", "SKU-0002"]


def test_build_with_empty_warehouse_scope_keeps_all_warehouses(
    session_factory: sessionmaker[Session],
) -> None:
    """warehouse_ids 为空列表：不过滤（全仓数据集）。"""
    _seed_local_data(session_factory)
    dataset = SqliteDatasetAdapter(session_factory).build(
        _request(date(2026, 6, 1), date(2026, 6, 30), [])
    )

    assert {m.warehouse_id for m in dataset.movements} == {"WH-01", "WH-02"}


def test_load_builds_default_request_covering_all_local_data(
    session_factory: sessionmaker[Session],
) -> None:
    """默认请求：期间覆盖库内全部事件/快照日期，仓库取全部有效仓。"""
    _seed_local_data(session_factory)
    request, dataset = SqliteDatasetAdapter(session_factory).load()

    # 最早事件 2026-05-15，最晚快照 2026-06-30 → 默认期间即数据全范围
    assert request.start_date == date(2026, 5, 15)
    assert request.end_date == date(2026, 6, 30)
    assert request.warehouse_ids == ["WH-01", "WH-02"]
    assert len(dataset.skus) == 2
    assert len(dataset.movements) == 6
    assert len(dataset.snapshots) == 2


def test_build_aggregates_lot_level_snapshots(
    session_factory: sessionmaker[Session],
) -> None:
    """同 (sku, 日期, 仓库) 的批次级快照聚合为一条时点总量。"""
    master = MasterDataRepository(session_factory)
    master.add_warehouse(warehouse_id="WH-01", name="主仓")
    master.add_sku(sku_id="SKU-0001", name="矿泉水 550ml")
    master.add_lot(sku_id="SKU-0001", lot_id="LOT-A")
    master.add_lot(sku_id="SKU-0001", lot_id="LOT-B")

    snapshots = StockSnapshotRepository(session_factory)
    snapshots.save_snapshot(
        snapshot_id="SNAP-A",
        snapshot_date="2026-06-30",
        sku_id="SKU-0001",
        warehouse_id="WH-01",
        quantity=Decimal(30),
        lot_id="LOT-A",
        inventory_value=Decimal("36.00"),
    )
    snapshots.save_snapshot(
        snapshot_id="SNAP-B",
        snapshot_date="2026-06-30",
        sku_id="SKU-0001",
        warehouse_id="WH-01",
        quantity=Decimal("40.5"),
        lot_id="LOT-B",
        inventory_value=Decimal("48.60"),
    )

    dataset = SqliteDatasetAdapter(session_factory).build(
        _request(date(2026, 6, 1), date(2026, 6, 30), [])
    )

    assert len(dataset.snapshots) == 1
    snapshot = dataset.snapshots[0]
    assert snapshot.quantity == Decimal("70.5")
    assert snapshot.inventory_value == Decimal("84.60")


def test_load_from_empty_database_returns_empty_dataset(
    session_factory: sessionmaker[Session],
) -> None:
    """空库：返回空数据集与当日单日期间请求，不抛异常。"""
    request, dataset = SqliteDatasetAdapter(session_factory).load()

    assert dataset.skus == []
    assert dataset.movements == []
    assert dataset.snapshots == []
    assert dataset.replenishment == []
    assert request.start_date == request.end_date
    assert request.warehouse_ids == []
