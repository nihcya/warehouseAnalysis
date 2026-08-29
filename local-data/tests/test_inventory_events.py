"""Task 3 测试：事件组迁移（0004_inventory_events）、事件幂等导入、余额投影与快照。

覆盖：空库升级/回滚、quantity > 0 CHECK、event_id 幂等重放（skipped 计数）、
投影重建确定性（重建两次/清空再重建一致）、负库存告警不阻断、
move_type 语义表、快照存取往返。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from local_data.connection import connect
from local_data.models import InventoryBalanceRow
from local_data.projection import BalanceProjectionService
from local_data.repository import (
    InventoryEventCreate,
    InventoryEventRepository,
    MasterDataRepository,
    StockSnapshotRepository,
)
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

EVENT_TABLES = {
    "inventory_event",
    "inventory_event_line",
    "stock_snapshot",
    "purchase_order",
    "purchase_order_line",
    "inventory_balance",
}

SKU = "SKU-001"
WAREHOUSE = "WH-01"
LOCATION = "A-01-01"
LOT = "LOT-001"


def _table_names(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).all()
    return {name for (name,) in rows}


def _schema_version(engine) -> str:
    with engine.connect() as conn:
        meta = dict(conn.execute(text("SELECT key, value FROM local_meta")).all())
    return meta["db_schema_version"]


def test_upgrade_0004_inventory_events_then_downgrade(
    alembic_cfg: Config, data_dir: Path
) -> None:
    """空库升级到 0004：6 表齐、索引在、版本 local-0004；-1 回滚后表全消失。"""
    engine, _factory = connect(data_dir)
    try:
        command.upgrade(alembic_cfg, "0004_inventory_events")
        assert EVENT_TABLES <= _table_names(engine)
        assert _schema_version(engine) == "local-0004"
        with engine.connect() as conn:
            indexes = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='index'")
                ).all()
            }
        assert "ix_inventory_event_sku_id_warehouse_id_occurred_at" in indexes

        command.downgrade(alembic_cfg, "-1")
        assert not EVENT_TABLES & _table_names(engine)
        assert _schema_version(engine) == "local-0003"
        assert "sku" in _table_names(engine)  # 主数据组保留
    finally:
        engine.dispose()


def _seed_master_data(session_factory: sessionmaker[Session]) -> None:
    """写入满足外键的主数据（SKU/仓库/库位/批次）。"""
    repo = MasterDataRepository(session_factory)
    repo.add_sku(sku_id=SKU, name="苏打水 330ml", unit_cost=Decimal("2.50"))
    repo.add_warehouse(warehouse_id=WAREHOUSE, name="一号仓")
    repo.add_location(warehouse_id=WAREHOUSE, location_id=LOCATION)
    repo.add_lot(
        sku_id=SKU,
        lot_id=LOT,
        production_date="2026-08-01",
        expiry_date="2026-09-01",
        received_at="2026-08-02T08:00:00+00:00",
    )


def _event(
    event_id: str,
    move_type: str,
    quantity: str,
    day: int,
    *,
    location_id: str | None = LOCATION,
    lot_id: str | None = LOT,
    reversal_of: str | None = None,
) -> InventoryEventCreate:
    return InventoryEventCreate(
        event_id=event_id,
        sku_id=SKU,
        warehouse_id=WAREHOUSE,
        location_id=location_id,
        lot_id=lot_id,
        move_type=move_type,
        quantity=Decimal(quantity),
        occurred_at=f"2026-08-{day:02d}T08:00:00+00:00",
        source="TEST",
        reversal_of=reversal_of,
    )


def test_event_quantity_must_be_positive(session_factory: sessionmaker[Session]) -> None:
    """quantity <= 0 被 CHECK 拒绝（0 与负数均违例）。"""
    _seed_master_data(session_factory)
    repo = InventoryEventRepository(session_factory)
    with pytest.raises(IntegrityError):
        repo.upsert_events([_event("E-ZERO", "INBOUND", "0", 1)])
    with pytest.raises(IntegrityError):
        repo.upsert_events([_event("E-NEG", "INBOUND", "-3", 1)])
    assert repo.get_event("E-ZERO") is None


def test_upsert_events_idempotent_skip(session_factory: sessionmaker[Session]) -> None:
    """同 event_id 二次导入被跳过并计数，不报错；批内重复同样计数。"""
    _seed_master_data(session_factory)
    repo = InventoryEventRepository(session_factory)
    first = repo.upsert_events([_event("E-1", "INBOUND", "10", 1)])
    assert (first.inserted, first.skipped) == (1, 0)

    # 同一 event_id 二次导入：skipped = 1，历史事件保持不变
    second = repo.upsert_events([_event("E-1", "INBOUND", "10", 1)])
    assert (second.inserted, second.skipped) == (0, 1)

    # 混合批次：1 条已存在 + 1 条批内重复 + 2 条新事件
    third = repo.upsert_events(
        [
            _event("E-1", "INBOUND", "10", 1),
            _event("E-2", "OUTBOUND", "4", 2),
            _event("E-2", "OUTBOUND", "4", 2),  # 批内重复
            _event("E-3", "INBOUND", "6", 3),
        ]
    )
    assert (third.inserted, third.skipped) == (2, 2)

    assert repo.get_event("E-1") is not None
    assert repo.get_event("E-2") is not None
    assert repo.get_event("E-3") is not None
    assert len(repo.list_events()) == 3


def _balance_rows(session_factory: sessionmaker[Session]) -> list[tuple]:
    """读当前投影为可比对元组（维度 + 数量 + as_of）。"""
    with session_factory() as session:
        rows = session.execute(
            select(InventoryBalanceRow).order_by(
                InventoryBalanceRow.sku_id,
                InventoryBalanceRow.warehouse_id,
                InventoryBalanceRow.location_id,
                InventoryBalanceRow.lot_id,
                InventoryBalanceRow.id,
            )
        ).scalars()
        return [
            (
                row.sku_id,
                row.warehouse_id,
                row.location_id,
                row.lot_id,
                row.on_hand_qty,
                row.available_qty,
                row.reserved_qty,
                row.as_of_event_id,
            )
            for row in rows
        ]


def test_projection_rebuild_deterministic(session_factory: sessionmaker[Session]) -> None:
    """投影重建确定性：重建两次、清空再重建，结果均一致。"""
    _seed_master_data(session_factory)
    repo = InventoryEventRepository(session_factory)
    repo.upsert_events(
        [
            _event("E-IN-1", "INBOUND", "100", 1),
            _event("E-OUT-1", "OUTBOUND", "30", 2),
            _event("E-RET-1", "RETURN", "5", 3),
            _event("E-NO-LOC", "INBOUND", "8", 4, location_id=None, lot_id=None),
        ]
    )
    service = BalanceProjectionService(session_factory)

    first = service.rebuild()
    rows_first = _balance_rows(session_factory)
    assert first.event_count == 4
    assert first.balance_count == 2  # (SKU, WH, 位置, 批次) 与 (SKU, WH, NULL, NULL)
    assert first.warnings == []

    second = service.rebuild()
    rows_second = _balance_rows(session_factory)
    assert (second.event_count, second.balance_count, second.warnings) == (
        first.event_count,
        first.balance_count,
        first.warnings,
    )
    assert rows_second == rows_first

    # 手动清空投影再重建（外部破坏后自愈）
    with session_factory() as session, session.begin():
        session.execute(delete(InventoryBalanceRow))
    assert _balance_rows(session_factory) == []
    service.rebuild()
    assert _balance_rows(session_factory) == rows_first

    by_dim = {(row[0], row[1], row[2], row[3]): row for row in rows_first}
    loc_lot = by_dim[(SKU, WAREHOUSE, LOCATION, LOT)]
    assert loc_lot[4] == Decimal(75)  # 100 - 30 + 5
    assert loc_lot[5] == Decimal(75)  # available = on_hand（M1 无预留）
    assert loc_lot[6] == Decimal(0)
    assert loc_lot[7] == "E-RET-1"
    no_dim = by_dim[(SKU, WAREHOUSE, None, None)]
    assert no_dim[4] == Decimal(8)
    assert no_dim[7] == "E-NO-LOC"


def test_projection_move_type_semantics(session_factory: sessionmaker[Session]) -> None:
    """move_type 语义表：入库加、出库减、退货加、报废减、调拨出入、
    盘点按实盘差额、冲销反转原事件方向。"""
    _seed_master_data(session_factory)
    repo = InventoryEventRepository(session_factory)
    repo.upsert_events(
        [
            _event("M-01", "INBOUND", "100", 1),
            _event("M-02", "OUTBOUND", "30", 2),  # 70
            _event("M-03", "RETURN", "5", 3),  # 75
            _event("M-04", "SCRAP", "3", 4),  # 72
            _event("M-05", "TRANSFER_OUT", "10", 5),  # 62
            _event("M-06", "TRANSFER_IN", "4", 6),  # 66
            _event("M-07", "ADJUSTMENT", "56", 7),  # 实盘 56：差额 -10
            _event("M-08", "REVERSAL", "30", 8, reversal_of="M-02"),  # 冲销出库 +30 → 86
        ]
    )
    result = BalanceProjectionService(session_factory).rebuild()
    assert result.warnings == []

    (row,) = _balance_rows(session_factory)
    assert row[4] == Decimal(86)
    assert row[5] == Decimal(86)
    assert row[7] == "M-08"


def test_projection_negative_balance_warns_not_blocks(
    session_factory: sessionmaker[Session],
) -> None:
    """出库导致负库存：投影照常计算并返回 warnings，不阻断（事实来源原则）。"""
    _seed_master_data(session_factory)
    repo = InventoryEventRepository(session_factory)
    repo.upsert_events(
        [
            _event("N-01", "INBOUND", "10", 1),
            _event("N-02", "OUTBOUND", "25", 2),  # 最终 -15
        ]
    )
    service = BalanceProjectionService(session_factory)
    result = service.rebuild()  # 不抛异常

    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert warning.startswith("NEGATIVE_BALANCE")
    assert SKU in warning and WAREHOUSE in warning
    assert "-15" in warning

    (row,) = _balance_rows(session_factory)
    assert row[4] == Decimal(-15)  # 负余额照常入库
    assert row[5] == Decimal(-15)


def test_snapshot_round_trip(session_factory: sessionmaker[Session]) -> None:
    """快照存取往返：字段（含 Decimal 数量/价值）读写一致。"""
    _seed_master_data(session_factory)
    repo = StockSnapshotRepository(session_factory)
    repo.save_snapshot(
        snapshot_id="SNAP-20260831-001",
        snapshot_date="2026-08-31",
        sku_id=SKU,
        warehouse_id=WAREHOUSE,
        location_id=LOCATION,
        lot_id=LOT,
        quantity=Decimal("120.5"),
        inventory_value=Decimal("301.25"),
        source="IMPORT_CSV",
    )

    loaded = repo.get_snapshot("SNAP-20260831-001")
    assert loaded is not None
    assert loaded.snapshot_date == "2026-08-31"
    assert loaded.sku_id == SKU
    assert loaded.warehouse_id == WAREHOUSE
    assert loaded.location_id == LOCATION
    assert loaded.lot_id == LOT
    assert loaded.quantity == Decimal("120.5")
    assert loaded.inventory_value == Decimal("301.25")
    assert loaded.source == "IMPORT_CSV"

    assert repo.get_snapshot("SNAP-NONE") is None
