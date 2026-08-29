"""本地库 Alembic 环境：sqlite URL 从数据目录推导。

优先级（与 alembic.ini 注释一致）：
1. ``-x url=...`` / ``-x data_dir=...`` 显式参数（CLI/测试使用）；
2. Config 上已设置的 ``sqlalchemy.url``（编程调用时由调用方设置）；
3. ``WORKBENCH_DATA_DIR`` 环境变量重定向；
4. 默认 ``%LOCALAPPDATA%\\WarehouseWorkbench\\data``（见 local_data.connection）。
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, make_url, pool

# 兼容未安装包时直接用 alembic CLI 的场景（src 布局）
_SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from local_data.connection import database_url, resolve_data_dir
from local_data.models import Base

config = context.config

# 按 ini 中的日志配置初始化（编程构造 Config 时不强制）
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _ensure_sqlite_parent_dir(url: str) -> str:
    """sqlite 文件库：确保数据库文件父目录存在（编程传入 URL 时目录可能未建）。"""
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database:
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    return url


def _resolve_database_url() -> str:
    """推导迁移目标库 URL：显式参数 > 已配置值 > 数据目录推导（含建目录）。"""
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("url"):
        return _ensure_sqlite_parent_dir(x_args["url"])
    if x_args.get("data_dir"):
        data_dir = Path(x_args["data_dir"])
        data_dir.mkdir(parents=True, exist_ok=True)
        return database_url(data_dir)
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return _ensure_sqlite_parent_dir(configured)
    data_dir = resolve_data_dir(None)
    data_dir.mkdir(parents=True, exist_ok=True)
    return database_url(data_dir)


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL 脚本，不连库。"""
    context.configure(
        url=_resolve_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite ALTER 有限，统一批处理渲染
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接目标库执行迁移。"""
    config.set_main_option("sqlalchemy.url", _resolve_database_url())
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
