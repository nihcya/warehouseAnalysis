"""Alembic 环境：连接串解析与迁移执行。

连接串优先级（兼容本地 / CI / 测试三种入口）：

1. ``alembic -x database_url=...`` 显式覆盖（最高）；
2. ``sqlalchemy.url``（alembic.ini 留空；测试经 ``set_main_option`` 注入）；
3. 环境变量 ``DATABASE_URL``（经 app.settings 读取，默认
   ``postgresql+psycopg://postgres:postgres@localhost:5432/warehouse_control``）。
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context

# 服务不以可分发包安装（tool.uv package=false），需把服务目录加入 sys.path 导入 app 包
SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

#: M0 迁移手写（无 autogenerate）；引入 ORM 模型后再挂载元数据
target_metadata = None


def _database_url() -> str:
    """按优先级解析本次迁移使用的数据库连接串。"""
    x_url = context.get_x_argument(as_dictionary=True).get("database_url")
    if x_url:
        return x_url
    ini_url = config.get_main_option("sqlalchemy.url")
    if ini_url:
        return ini_url
    return get_settings().DATABASE_URL


def run_migrations_offline() -> None:
    """离线模式：仅按连接串生成 SQL 脚本（不连接数据库）。"""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：直连控制库执行迁移。"""
    from sqlalchemy import create_engine

    engine = create_engine(_database_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
