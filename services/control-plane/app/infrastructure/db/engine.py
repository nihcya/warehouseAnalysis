"""PostgreSQL 引擎与可达性探测（M0 占位）。

M0 仅提供 /health 所需的最小连接能力；ORM 模型、Repository 与事务
管理在 M1+ 逐步引入（API 路由层不直接写 ORM）。
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

#: 健康检查探测的连接超时（秒），避免 /health 被不可达地址拖住
CONNECT_TIMEOUT_SECONDS = 3


def build_engine(database_url: str, *, connect_timeout: int = CONNECT_TIMEOUT_SECONDS) -> Engine:
    """构建控制库引擎（psycopg 同步驱动，探测用短超时）。"""
    return create_engine(
        database_url,
        connect_args={"connect_timeout": connect_timeout},
    )


def check_database_reachable(database_url: str) -> bool:
    """执行 SELECT 1 探测控制库可达性；任何异常均如实视为 down。"""
    engine = build_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 —— 健康检查对任何故障只能报 down，不区分原因
        return False
    finally:
        engine.dispose()
