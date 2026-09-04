"""云端控制库迁移测试（0001_control_meta 至 0005_config_task_heartbeat_sync）。

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
from sqlalchemy.exc import IntegrityError

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
    """在连接串上附加 ``options=-csearch_path=<schema>``，使迁移建在临时 schema 内。

    注意：``str(URL)`` 默认 ``hide_password=True`` 会把密码替换成字面量 ``***``，
    导致下游用错误密码认证失败；必须用 ``render_as_string(hide_password=False)``。
    """
    parsed = make_url(base_url)
    query = dict(parsed.query)
    query["options"] = f"-csearch_path={schema}"
    return parsed.set(query=query).render_as_string(hide_password=False)


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
    # 也不能用 command.upgrade(..., x=...)——x 仅 CLI 支持，API 不接受。
    # 正确通道：Config(attributes=...) 程序化注入，env.py 以最高优先级读取
    test_url = _with_search_path(postgres_url, schema)

    cfg = Config(str(ALEMBIC_INI), attributes={"database_url": test_url})

    try:
        command.upgrade(cfg, "head")

        with admin.connect() as connection:
            tables = _tables_in(connection, schema)
            assert {"control_meta", "control_enum"} <= tables
            # 0005（M3）：配置版本、任务、心跳与同步信封五表
            assert {
                "config_version",
                "task",
                "task_run",
                "heartbeat",
                "sync_envelope",
            } <= tables

            enum_rows = connection.execute(
                text(f'SELECT count(*) FROM "{schema}".control_enum')
            ).scalar_one()
            assert enum_rows > 0
            # 0005 新增 config_status 枚举种子（run_status / sync_status 已由 0001 播种）
            config_status_rows = connection.execute(
                text(
                    f"SELECT count(*) FROM \"{schema}\".control_enum WHERE kind = 'config_status'"
                )
            ).scalar_one()
            assert config_status_rows == 3

            schema_version = connection.execute(
                text(f'SELECT value FROM "{schema}".control_meta WHERE key = \'db_schema_version\'')
            ).scalar_one()
            # upgrade head 运行全部迁移（0001→0005），0005 把版本号升到 control-0005；
            # 断言最终版本，避免后续迁移新增时测试再次漂移。
            assert schema_version == "control-0005"

        command.downgrade(cfg, "base")

        with admin.connect() as connection:
            tables = _tables_in(connection, schema)
            assert "control_meta" not in tables
            assert "control_enum" not in tables
            # 0005 downgrade 完整回滚五张 M3 表
            assert not {
                "config_version",
                "task",
                "task_run",
                "heartbeat",
                "sync_envelope",
            } & tables
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            connection.commit()
        admin.dispose()


def test_m3_unique_constraints(postgres_url: str) -> None:
    """0005 唯一约束：配置版本不可覆盖、同步信封 event_id 全局唯一。

    铺底数据（tenant / device）满足外键；断言 ``uq_config_version_tenant_version``
    与 ``uq_sync_envelope_event_id`` 在数据库层拦截重复写入。
    """
    schema = f"control_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(postgres_url, connect_args={"connect_timeout": CONNECT_TIMEOUT})
    with admin.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.commit()

    cfg = Config(str(ALEMBIC_INI), attributes={"database_url": _with_search_path(postgres_url, schema)})

    def insert(sql: str) -> None:
        with admin.connect() as connection:
            connection.execute(text(sql))
            connection.commit()

    try:
        command.upgrade(cfg, "head")
        insert(
            f'INSERT INTO "{schema}".tenant (tenant_id, name, status) '
            "VALUES ('tnt_mig', '迁移测试商户', 'ACTIVE')"
        )
        insert(
            f'INSERT INTO "{schema}".device '
            "(device_id, tenant_id, device_type, name, fingerprint, status) "
            "VALUES ('dev_mig', 'tnt_mig', 'DESKTOP', '迁移测试设备', 'fp-mig', 'REGISTERED')"
        )
        insert(
            f'INSERT INTO "{schema}".config_version '
            "(config_version_id, tenant_id, version, content_json, sha256, signature, status) "
            "VALUES ('cfv_mig_1', 'tnt_mig', 'v1', '{}', "
            f"'{'a' * 64}', 'sig-1', 'PUBLISHED')"
        )
        insert(
            f'INSERT INTO "{schema}".sync_envelope '
            "(envelope_id, tenant_id, target_device_id, event_id, ciphertext, status) "
            "VALUES ('env_mig_1', 'tnt_mig', 'dev_mig', 'evt_mig_1', 'cipher-1', 'ENQUEUED')"
        )

        # 同一 (tenant_id, version) 的配置版本不可覆盖
        with pytest.raises(IntegrityError, match="uq_config_version_tenant_version"):
            insert(
                f'INSERT INTO "{schema}".config_version '
                "(config_version_id, tenant_id, version, content_json, sha256, signature, status) "
                "VALUES ('cfv_mig_2', 'tnt_mig', 'v1', '{}', "
                f"'{'b' * 64}', 'sig-2', 'PUBLISHED')"
            )

        # event_id 全局唯一（客户端幂等落库的数据库兜底）
        with pytest.raises(IntegrityError, match="uq_sync_envelope_event_id"):
            insert(
                f'INSERT INTO "{schema}".sync_envelope '
                "(envelope_id, tenant_id, target_device_id, event_id, ciphertext, status) "
                "VALUES ('env_mig_2', 'tnt_mig', 'dev_mig', 'evt_mig_1', 'cipher-2', 'ENQUEUED')"
            )
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            connection.commit()
        admin.dispose()
