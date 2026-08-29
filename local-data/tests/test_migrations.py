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

LOCAL_TABLES = {"local_meta", "analysis_run", "analysis_result"}


def _table_names(engine) -> set[str]:
    """查询当前库中的表名集合。"""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).all()
    return {name for (name,) in rows}


def test_upgrade_head_then_downgrade_base(alembic_cfg: Config, data_dir: Path) -> None:
    """upgrade head：三表存在、种子齐、索引在；downgrade base：表全部消失。"""
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
        assert meta["db_schema_version"] == "local-0002"
        assert meta["single_primary_workbench"] == "1"
        uuid.UUID(meta["install_instance_id"])  # 合法且非空 UUID
        assert "ix_analysis_result_run_id_result_type_sku_id" in indexes

        command.downgrade(alembic_cfg, "base")
        assert not LOCAL_TABLES & _table_names(engine)
    finally:
        engine.dispose()


def test_downgrade_one_step_keeps_meta(alembic_cfg: Config, data_dir: Path) -> None:
    """downgrade -1：analysis 两表消失，local_meta 保留且版本回到 local-0001。"""
    engine, _factory = connect(data_dir)
    try:
        command.upgrade(alembic_cfg, "head")
        command.downgrade(alembic_cfg, "-1")
        tables = _table_names(engine)
        assert "local_meta" in tables
        assert "analysis_run" not in tables
        assert "analysis_result" not in tables
        with engine.connect() as conn:
            meta = dict(conn.execute(text("SELECT key, value FROM local_meta")).all())
        assert meta["db_schema_version"] == "local-0001"
    finally:
        engine.dispose()
