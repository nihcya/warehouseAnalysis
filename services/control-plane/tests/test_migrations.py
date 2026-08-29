"""云端控制库迁移测试（0001_control_meta）。

本地无 PostgreSQL 时跳过（CI 通过 PostgreSQL service 容器执行）；
可用环境变量 ``CONTROL_PLANE_TEST_DATABASE_URL`` 指定测试库。
测试在临时 schema 内执行 upgrade → 验证 → downgrade → 验证，互不污染。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from app.settings import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

SERVICE_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = SERVICE_ROOT / "alembic.ini"

SKIP_REASON = "PostgreSQL not available locally; runs in CI service container"
CONNECT_TIMEOUT = 3


def _database_url() -> str:
    """测试库连接串：环境变量优先，回退到应用配置。"""
    return os.environ.get("CONTROL_PLANE_TEST_DATABASE_URL") or get_settings().DATABASE_URL


def _connectable(url: str) -> bool:
    """探测 PostgreSQL 是否可达（仅支持 postgresql 方言）。"""
    if not url.startswith("postgresql"):
        return False
    try:
        engine = create_engine(url, connect_args={"connect_timeout": CONNECT_TIMEOUT})
    except Exception:  # noqa: BLE001 —— 任何配置问题都按“不可用”处理并跳过
        return False
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        engine.dispose()


def _with_search_path(base_url: str, schema: str) -> str:
    """在连接串上附加 ``options=-csearch_path=<schema>``，使迁移建在临时 schema 内。"""
    parsed = make_url(base_url)
    query = dict(parsed.query)
    query["options"] = f"-csearch_path={schema}"
    return str(parsed.set(query=query))


def _tables_in(connection: Any, schema: str) -> set[str]:
    rows = connection.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = :schema"),
        {"schema": schema},
    )
    return {row[0] for row in rows}


@pytest.fixture()
def postgres_url() -> str:
    """可达的 PostgreSQL 连接串；不可达则跳过测试。"""
    url = _database_url()
    if not _connectable(url):
        pytest.skip(SKIP_REASON)
    return url


def test_control_meta_upgrade_downgrade_roundtrip(postgres_url: str) -> None:
    """空库（临时 schema）：upgrade head 建表并播种，downgrade base 完整回滚。"""
    schema = f"control_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(postgres_url, connect_args={"connect_timeout": CONNECT_TIMEOUT})
    with admin.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.commit()

    # 注意：不能用 set_main_option 注入含 URL 编码（%3D 等）的连接串——
    # configparser 的 BasicInterpolation 会把 % 当插值符直接抛 ValueError；
    # 改走 -x database_url 覆盖（env.py 优先级最高，不经过 ini 解析）
    test_url = _with_search_path(postgres_url, schema)

    cfg = Config(str(ALEMBIC_INI))

    try:
        command.upgrade(cfg, "head", x={"database_url": test_url})

        with admin.connect() as connection:
            tables = _tables_in(connection, schema)
            assert {"control_meta", "control_enum"} <= tables

            enum_rows = connection.execute(
                text(f'SELECT count(*) FROM "{schema}".control_enum')
            ).scalar_one()
            assert enum_rows > 0

            schema_version = connection.execute(
                text(f'SELECT value FROM "{schema}".control_meta WHERE key = \'db_schema_version\'')
            ).scalar_one()
            assert schema_version == "control-0001"

        command.downgrade(cfg, "base", x={"database_url": test_url})

        with admin.connect() as connection:
            tables = _tables_in(connection, schema)
            assert "control_meta" not in tables
            assert "control_enum" not in tables
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            connection.commit()
        admin.dispose()
