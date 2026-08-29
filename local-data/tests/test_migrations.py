"""迁移测试：alembic upgrade head 建表与种子，downgrade base 完整回滚。

全部用 alembic.command API 编程调用（Config 指向 tmp 库）。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from local_data.connection import connect
from sqlalchemy import text

LOCAL_TABLES = {
    "local_meta",
    "analysis_run",
    "analysis_result",
    # 0003_master_data（M1 Task 2）
    "sku",
    "barcode",
    "warehouse",
    "location",
    "supplier",
    "supplier_sku",
    "lot",
    # 0004_inventory_events（M1 Task 3）
    "inventory_event",
    "inventory_event_line",
    "stock_snapshot",
    "purchase_order",
    "purchase_order_line",
    "inventory_balance",
    # 0005_import（M1 Task 4）
    "import_batch",
    "import_error",
    # 0006_report_backup（M1 Task 8/9）
    "report_artifact",
    "backup_record",
}


def _table_names(engine) -> set[str]:
    """查询当前库中的表名集合。"""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).all()
    return {name for (name,) in rows}


def test_upgrade_head_then_downgrade_base(alembic_cfg: Config, data_dir: Path) -> None:
    """upgrade head：全部表存在、种子齐、索引在；downgrade base：表全部消失。"""
    engine, _factory = connect(data_dir)
    try:
        # 未迁移前无业务表
        assert not LOCAL_TABLES & _table_names(engine)

        command.upgrade(alembic_cfg, "head")
        assert LOCAL_TABLES <= _table_names(engine)

        with engine.connect() as conn:
            meta = dict(conn.execute(text("SELECT key, value FROM local_meta")).all())
            indexes = {
                row[0]
                for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'")).all()
            }
        assert meta["db_schema_version"] == "local-0006"
        assert meta["single_primary_workbench"] == "1"
        uuid.UUID(meta["install_instance_id"])  # 合法且非空 UUID
        assert "ix_analysis_result_run_id_result_type_sku_id" in indexes
        assert "ix_inventory_event_sku_id_warehouse_id_occurred_at" in indexes

        command.downgrade(alembic_cfg, "base")
        assert not LOCAL_TABLES & _table_names(engine)
    finally:
        engine.dispose()


def test_downgrade_one_step_keeps_meta(alembic_cfg: Config, data_dir: Path) -> None:
    """downgrade -1（head=0006_report_backup）：报告/备份两表消失，
    其余表与 local_meta 保留，版本回到 local-0005。"""
    engine, _factory = connect(data_dir)
    try:
        command.upgrade(alembic_cfg, "head")
        command.downgrade(alembic_cfg, "-1")
        tables = _table_names(engine)
        assert "local_meta" in tables
        assert "analysis_run" in tables
        assert "sku" in tables
        assert "inventory_event" in tables
        assert "import_batch" in tables
        assert "import_error" in tables
        assert "report_artifact" not in tables
        assert "backup_record" not in tables
        with engine.connect() as conn:
            meta = dict(conn.execute(text("SELECT key, value FROM local_meta")).all())
        assert meta["db_schema_version"] == "local-0005"
    finally:
        engine.dispose()
