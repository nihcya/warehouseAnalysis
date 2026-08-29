"""Task 2 测试：主数据组迁移（0003_master_data）与 MasterDataRepository。

覆盖：空库升级/回滚、UNIQUE 冲突抛错、lot 日期 CHECK 违例、
supplier_sku 非负 CHECK、SKU 存取往返、条码唯一映射校验。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from local_data.connection import connect
from local_data.repository import MasterDataRepository
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

MASTER_TABLES = {
    "sku",
    "barcode",
    "warehouse",
    "location",
    "supplier",
    "supplier_sku",
    "lot",
}


def _table_names(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).all()
    return {name for (name,) in rows}


def _schema_version(engine) -> str:
    with engine.connect() as conn:
        meta = dict(conn.execute(text("SELECT key, value FROM local_meta")).all())
    return meta["db_schema_version"]


def test_upgrade_0003_master_data_then_downgrade(alembic_cfg: Config, data_dir: Path) -> None:
    """空库升级到 0003：7 表齐、索引在、版本 local-0003；-1 回滚后表全消失。"""
    engine, _factory = connect(data_dir)
    try:
        assert not MASTER_TABLES & _table_names(engine)  # 空库无主数据表

        command.upgrade(alembic_cfg, "0003_master_data")
        assert MASTER_TABLES <= _table_names(engine)
        assert _schema_version(engine) == "local-0003"
        with engine.connect() as conn:
            indexes = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='index'")
                ).all()
            }
        assert {"ix_sku_category", "ix_sku_is_active"} <= indexes

        command.downgrade(alembic_cfg, "-1")
        assert not MASTER_TABLES & _table_names(engine)
        assert _schema_version(engine) == "local-0002"
    finally:
        engine.dispose()


def _make_repo(session_factory: sessionmaker[Session]) -> MasterDataRepository:
    return MasterDataRepository(session_factory)


def test_sku_round_trip(session_factory: sessionmaker[Session]) -> None:
    """SKU 写入后按 sku_id 查询，字段往返一致（含 Decimal 金额/倍率）。"""
    repo = _make_repo(session_factory)
    saved = repo.add_sku(
        sku_id="SKU-001",
        name="苏打水 330ml",
        category="饮料",
        sub_category="气泡水",
        unit="瓶",
        unit_scale=Decimal(1),
        unit_cost=Decimal("2.50"),
        industry="食品饮料",
    )
    assert saved.id is not None

    loaded = repo.get_sku_by_sku_id("SKU-001")
    assert loaded is not None
    assert loaded.sku_id == "SKU-001"
    assert loaded.name == "苏打水 330ml"
    assert loaded.category == "饮料"
    assert loaded.sub_category == "气泡水"
    assert loaded.unit == "瓶"
    assert loaded.unit_scale == Decimal(1)
    assert loaded.unit_cost == Decimal("2.50")
    assert loaded.industry == "食品饮料"
    assert loaded.is_active is True
    assert loaded.created_at is not None

    assert repo.get_sku_by_sku_id("SKU-NONE") is None


def test_duplicate_sku_id_rejected(session_factory: sessionmaker[Session]) -> None:
    """重复 sku_id 写入触发唯一约束错误（IntegrityError）。"""
    repo = _make_repo(session_factory)
    repo.add_sku(sku_id="SKU-DUP", name="第一个")
    with pytest.raises(IntegrityError):
        repo.add_sku(sku_id="SKU-DUP", name="第二个")


def test_duplicate_location_in_same_warehouse_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    """同仓重复 location_id 触发组合唯一约束错误。"""
    repo = _make_repo(session_factory)
    repo.add_warehouse(warehouse_id="WH-01", name="一号仓")
    repo.add_location(warehouse_id="WH-01", location_id="A-01-01")
    with pytest.raises(IntegrityError):
        repo.add_location(warehouse_id="WH-01", location_id="A-01-01")


def test_lot_expiry_before_production_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    """生产日期晚于有效期被 CHECK 拒绝；合法日期与缺省日期可正常写入。"""
    repo = _make_repo(session_factory)
    repo.add_sku(sku_id="SKU-LOT", name="鲜奶 1L")

    with pytest.raises(IntegrityError):
        repo.add_lot(
            sku_id="SKU-LOT",
            lot_id="LOT-BAD",
            production_date="2026-08-10",
            expiry_date="2026-08-01",  # 有效期早于生产日期
            received_at="2026-08-10T08:00:00+00:00",
        )

    ok = repo.add_lot(
        sku_id="SKU-LOT",
        lot_id="LOT-OK",
        production_date="2026-08-01",
        expiry_date="2026-08-10",
        received_at="2026-08-02T08:00:00+00:00",
    )
    assert ok.lot_id == "LOT-OK"
    assert ok.expiry_date == "2026-08-10"

    no_dates = repo.add_lot(sku_id="SKU-LOT", lot_id="LOT-NA")  # 日期缺省放行
    assert no_dates.production_date is None


def test_supplier_sku_negative_param_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    """补货参数为负被非负 CHECK 拒绝；非负参数正常写入。"""
    repo = _make_repo(session_factory)
    repo.add_supplier(supplier_id="SUP-01", name="华东供应商")
    repo.add_sku(sku_id="SKU-SUP", name="补货品")

    with pytest.raises(IntegrityError):
        repo.add_supplier_sku(
            supplier_id="SUP-01", sku_id="SKU-SUP", order_cost=Decimal("-1.00")
        )

    row = repo.add_supplier_sku(
        supplier_id="SUP-01",
        sku_id="SKU-SUP",
        lead_time_days=7,
        moq=Decimal(10),
        pack_size=Decimal(12),
        order_cost=Decimal(0),
        holding_cost=Decimal("0.5"),
        is_preferred=True,
    )
    assert row.lead_time_days == 7
    assert row.moq == Decimal(10)
    assert row.is_preferred is True


def test_barcode_maps_single_active_sku(session_factory: sessionmaker[Session]) -> None:
    """条码唯一映射校验：barcode 只映射一个有效 SKU。

    - 有效映射（映射行与 SKU 均激活）返回该 SKU；
    - SKU 停用后同一 barcode 返回 None（无有效映射）；
    - 重复 barcode 写入触发 UNIQUE 冲突。
    """
    repo = _make_repo(session_factory)
    repo.add_sku(sku_id="SKU-A", name="商品 A")
    repo.add_sku(sku_id="SKU-B", name="商品 B", is_active=False)
    repo.add_barcode(barcode="6901234567890", sku_id="SKU-A")
    repo.add_barcode(barcode="6909876543210", sku_id="SKU-B")  # 映射到停用 SKU

    active = repo.get_active_sku_by_barcode("6901234567890")
    assert active is not None
    assert active.sku_id == "SKU-A"

    assert repo.get_active_sku_by_barcode("6909876543210") is None  # SKU 已停用
    assert repo.get_active_sku_by_barcode("0000000000000") is None  # 无映射

    with pytest.raises(IntegrityError):
        repo.add_barcode(barcode="6901234567890", sku_id="SKU-B")
