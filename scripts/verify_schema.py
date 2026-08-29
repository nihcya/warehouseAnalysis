"""verify_schema.py：M0 迁移结构验证（评审报告 §3.3 建库完成证据 / §8 P0-1）。

对两库执行 ``alembic upgrade head`` 后的迁移结果做机器检查：

- 本地业务库（SQLite）：tmp 目录建库 → upgrade head → 检查表存在性、
  ``alembic_version`` 值、local_meta 种子、关键约束（``analysis_run.run_id``
  UNIQUE、``analysis_result`` 复合索引与外键、主键、NOT NULL）与约束实际生效
  （重复 run_id / 孤儿 run_id 插入被拒）；
- 云端控制库（PostgreSQL）：连接探测（``CONTROL_PLANE_TEST_DATABASE_URL``
  或应用配置 ``DATABASE_URL``）→ 在临时 schema 内 upgrade head → 检查表存在性、
  ``alembic_version``、control_meta/control_enum 种子与 ``code`` UNIQUE →
  downgrade base 验证完整回滚；结束后 drop 临时 schema，不污染目标库。
  本地无 PostgreSQL 时打印 skip（CI 由 service 容器执行真实校验）。

运行（仓库根）::

    uv run python scripts/verify_schema.py

退出码：0 = 全部通过（云端不可达视为 skip）；1 = 存在失败项。
迁移调用方式与 ``local-data/tests/conftest.py``、``local-data/tests/test_migrations.py``
及 ``services/control-plane/tests/test_migrations.py`` 保持一致。
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DATA_ROOT = REPO_ROOT / "local-data"
CONTROL_PLANE_ROOT = REPO_ROOT / "services" / "control-plane"

#: 本地库 upgrade head 后应存在的表（含 Alembic 自动维护的版本表）
EXPECTED_LOCAL_TABLES = {"local_meta", "analysis_run", "analysis_result", "alembic_version"}
#: 云端库 upgrade head 后应存在的表
EXPECTED_CONTROL_TABLES = {"control_meta", "control_enum", "alembic_version"}
#: 两库 alembic_version 期望值
LOCAL_HEAD = "0002_analysis_m0"
CONTROL_HEAD = "control_0001"
#: 云端连接串环境变量（与 services/control-plane/tests/test_migrations.py 一致）
CONTROL_TEST_URL_ENV = "CONTROL_PLANE_TEST_DATABASE_URL"
#: PostgreSQL 连接探测超时（秒）
CONNECT_TIMEOUT = 3
#: control_enum 种子期望行数（task/run/device/sync_status + move_type 五类合计）
EXPECTED_ENUM_ROWS = 36
EXPECTED_ENUM_KINDS = {"task_status", "run_status", "device_status", "sync_status", "move_type"}

PASSED: list[str] = []
FAILED: list[str] = []


def check(ok: bool, message: str) -> None:
    """记录并打印一项检查结果。"""
    if ok:
        PASSED.append(message)
        print(f"  [ok]   {message}")
    else:
        FAILED.append(message)
        print(f"  [FAIL] {message}")


def verify_local_sqlite() -> None:
    """本地业务库：tmp 目录建库 → alembic upgrade head → 结构与约束检查。"""
    from alembic import command
    from alembic.config import Config
    from local_data.connection import connect, database_url
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    print("\n[local] 本地业务库（SQLite）：tmp 目录建库并 alembic upgrade head")
    with tempfile.TemporaryDirectory(prefix="verify-schema-local-") as tmp:
        data_dir = Path(tmp) / "wb-data"
        cfg = Config(str(LOCAL_DATA_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(LOCAL_DATA_ROOT / "alembic"))
        cfg.set_main_option("sqlalchemy.url", database_url(data_dir))
        command.upgrade(cfg, "head")

        engine, _factory = connect(data_dir)
        try:
            with engine.connect() as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table'")
                    )
                }
                check(
                    EXPECTED_LOCAL_TABLES <= tables,
                    f"表存在性：{sorted(EXPECTED_LOCAL_TABLES)} 全部建表",
                )

                version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                check(
                    version == LOCAL_HEAD,
                    f"alembic_version = {version!r}（期望 {LOCAL_HEAD!r}）",
                )

                meta: dict[str, str] = {
                    row[0]: row[1]
                    for row in conn.execute(text("SELECT key, value FROM local_meta"))
                }
                check(
                    meta.get("db_schema_version") == "local-0002",
                    "local_meta 种子 db_schema_version = local-0002",
                )
                try:
                    uuid.UUID(meta.get("install_instance_id", ""))
                    install_id_ok = True
                except ValueError:
                    install_id_ok = False
                check(install_id_ok, "local_meta 种子 install_instance_id 为合法 UUID")
                check(
                    meta.get("single_primary_workbench") == "1",
                    "local_meta 种子 single_primary_workbench = 1",
                )

                # 唯一约束与索引（SQLite 把表级 UNIQUE 实现为 sqlite_autoindex_*，
                # 约束名保留在 DDL 文本中，因此分两步核对：DDL 约束名 + 唯一索引覆盖列）
                ddl = conn.execute(
                    text("SELECT sql FROM sqlite_master WHERE name='analysis_run'")
                ).scalar_one()
                check(
                    "CONSTRAINT uq_analysis_run_run_id UNIQUE (run_id)" in ddl,
                    "analysis_run DDL 含 CONSTRAINT uq_analysis_run_run_id UNIQUE (run_id)",
                )
                unique_indexes = [
                    row[1]
                    for row in conn.execute(text("PRAGMA index_list('analysis_run')"))
                    if row[2] == 1
                ]
                run_id_unique = False
                for idx_name in unique_indexes:
                    idx_cols = [
                        row[2]
                        for row in conn.execute(text(f"PRAGMA index_info('{idx_name}')"))
                    ]
                    if idx_cols == ["run_id"]:
                        run_id_unique = True
                check(run_id_unique, "analysis_run.run_id 上存在唯一索引（仅覆盖 run_id 列）")
                result_idx = {
                    row[1]
                    for row in conn.execute(text("PRAGMA index_list('analysis_result')"))
                }
                check(
                    "ix_analysis_result_run_id_result_type_sku_id" in result_idx,
                    "analysis_result 复合索引 ix_analysis_result_run_id_result_type_sku_id 存在",
                )

                # 外键定义（PRAGMA foreign_key_list：table/from/to）
                fk_rows = [
                    (row[2], row[3], row[4])
                    for row in conn.execute(text("PRAGMA foreign_key_list('analysis_result')"))
                ]
                check(
                    ("analysis_run", "run_id", "run_id") in fk_rows,
                    "analysis_result.run_id 外键 → analysis_run.run_id 存在",
                )

                # 主键与 NOT NULL（PRAGMA table_info：name → (type, notnull, pk)）
                run_cols = {
                    row[1]: (row[2], row[3], row[5])
                    for row in conn.execute(text("PRAGMA table_info('analysis_run')"))
                }
                check(
                    run_cols.get("id", ("", 0, 0))[2] == 1,
                    "analysis_run 主键 = id（INTEGER 自增）",
                )
                check(
                    run_cols.get("run_id", ("", 0, 0))[1] == 1,
                    "analysis_run.run_id NOT NULL",
                )

            # 功能性约束验证：插入后回滚，不在临时库留数据
            def _insert_run(run_id: str) -> None:
                conn.execute(
                    text(
                        "INSERT INTO analysis_run"
                        " (run_id, engine_version, formula_version, status, created_at, updated_at)"
                        " VALUES (:rid, '0.1.0', '0.1.0', 'SUCCEEDED',"
                        " '2026-08-29T00:00:00+00:00', '2026-08-29T00:00:00+00:00')"
                    ),
                    {"rid": run_id},
                )

            with engine.connect() as conn:
                first_ok = False
                duplicate_rejected = False
                try:
                    _insert_run("run-verify-1")
                    first_ok = True
                    _insert_run("run-verify-1")
                except IntegrityError:
                    duplicate_rejected = True
                finally:
                    conn.rollback()
                check(
                    first_ok and duplicate_rejected,
                    "analysis_run.run_id 重复插入被拒（UNIQUE 实际生效）",
                )

            with engine.connect() as conn:
                try:
                    conn.execute(
                        text(
                            "INSERT INTO analysis_result"
                            " (run_id, result_type, metric_json, warning_json, created_at)"
                            " VALUES ('run-not-exist', 'full_result', '{}', '[]',"
                            " '2026-08-29T00:00:00+00:00')"
                        )
                    )
                    orphan_rejected = False
                except IntegrityError:
                    orphan_rejected = True
                finally:
                    conn.rollback()
                check(
                    orphan_rejected,
                    "analysis_result 孤儿 run_id 插入被拒（外键实际生效，PRAGMA foreign_keys=ON）",
                )
        finally:
            engine.dispose()


def _control_database_url() -> str:
    """云端连接串：环境变量优先，回退到应用配置（与迁移测试同口径）。"""
    env_url = os.environ.get(CONTROL_TEST_URL_ENV)
    if env_url:
        return env_url
    if str(CONTROL_PLANE_ROOT) not in sys.path:
        sys.path.insert(0, str(CONTROL_PLANE_ROOT))
    from app.settings import get_settings

    return get_settings().DATABASE_URL


def _connectable(url: str) -> bool:
    """探测 PostgreSQL 是否可达（仅支持 postgresql 方言）。"""
    from sqlalchemy import create_engine, text

    if not url.startswith("postgresql"):
        return False
    try:
        engine = create_engine(url, connect_args={"connect_timeout": CONNECT_TIMEOUT})
    except Exception:  # noqa: BLE001 —— 任何配置问题都按“不可达”处理并跳过
        return False
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        engine.dispose()


def verify_control_postgres() -> None:
    """云端控制库：临时 schema 内 upgrade head → 结构/种子检查 → downgrade 回滚。"""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    print("\n[cloud] 云端控制库（PostgreSQL）：连接探测 → 临时 schema 内 alembic upgrade head")
    url = _control_database_url()
    print(f"  目标：{make_url(url).render_as_string(hide_password=True)}")
    if not _connectable(url):
        print("  [skip] PostgreSQL 不可达（本地无 PG；CI 由 service 容器执行真实校验）")
        return

    schema = f"control_verify_{uuid.uuid4().hex[:12]}"
    admin = create_engine(url, connect_args={"connect_timeout": CONNECT_TIMEOUT})
    with admin.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.commit()

    parsed = make_url(url)
    query = dict(parsed.query)
    query["options"] = f"-csearch_path={schema}"
    cfg = Config(str(CONTROL_PLANE_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", str(parsed.set(query=query)))

    try:
        command.upgrade(cfg, "head")

        with admin.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables"
                        " WHERE table_schema = :schema"
                    ),
                    {"schema": schema},
                )
            }
            check(
                EXPECTED_CONTROL_TABLES <= tables,
                f"表存在性：{sorted(EXPECTED_CONTROL_TABLES)} 全部建表",
            )

            version = connection.execute(
                text(f'SELECT version_num FROM "{schema}".alembic_version')
            ).scalar_one()
            check(
                version == CONTROL_HEAD,
                f"alembic_version = {version!r}（期望 {CONTROL_HEAD!r}）",
            )

            meta: dict[str, str] = {
                row[0]: row[1]
                for row in connection.execute(
                    text(f'SELECT key, value FROM "{schema}".control_meta')
                )
            }
            check(
                meta.get("db_schema_version") == "control-0001",
                "control_meta 种子 db_schema_version = control-0001",
            )
            check(
                meta.get("control_plane_version") == "0.1.0",
                "control_meta 种子 control_plane_version = 0.1.0",
            )

            enum_count = connection.execute(
                text(f'SELECT count(*) FROM "{schema}".control_enum')
            ).scalar_one()
            check(
                enum_count == EXPECTED_ENUM_ROWS,
                f"control_enum 种子行数 = {enum_count}（期望 {EXPECTED_ENUM_ROWS}）",
            )
            kinds = {
                row[0]
                for row in connection.execute(
                    text(f'SELECT DISTINCT kind FROM "{schema}".control_enum')
                )
            }
            check(
                kinds == EXPECTED_ENUM_KINDS,
                "control_enum 五类枚举齐备（task/run/device/sync_status + move_type）",
            )

            code_unique = connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.table_constraints"
                    " WHERE table_schema = :schema AND table_name = 'control_enum'"
                    " AND constraint_type = 'UNIQUE'"
                ),
                {"schema": schema},
            ).scalar_one()
            check(code_unique >= 1, "control_enum.code UNIQUE 约束存在")

            meta_pk = connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.table_constraints"
                    " WHERE table_schema = :schema AND table_name = 'control_meta'"
                    " AND constraint_type = 'PRIMARY KEY'"
                ),
                {"schema": schema},
            ).scalar_one()
            check(meta_pk >= 1, "control_meta.key 主键存在")

        # 完整可逆验证：downgrade base 后临时 schema 内应无残留表
        command.downgrade(cfg, "base")
        with admin.connect() as connection:
            remain = connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables"
                    " WHERE table_schema = :schema"
                ),
                {"schema": schema},
            ).scalar_one()
            check(remain == 0, "downgrade base 后临时 schema 内表全部清除（完整可逆）")
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            connection.commit()
        admin.dispose()


def main() -> int:
    print("verify_schema.py：M0 迁移结构验证（本地 SQLite + 云端 PostgreSQL）")
    verify_local_sqlite()
    verify_control_postgres()

    print("\n===== 结果 =====")
    print(f"通过 {len(PASSED)} 项；失败 {len(FAILED)} 项")
    if FAILED:
        print("失败项：")
        for item in FAILED:
            print(f"  - {item}")
        return 1
    print("全部通过（云端不可达时按 skip 处理，不算失败）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
