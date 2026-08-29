"""本地 SQLite 连接工厂（主基线 §35.3 建库原则）。

数据目录解析优先级：
1. 显式传入的 ``data_dir`` 参数（组合根与测试使用）；
2. 环境变量 ``WORKBENCH_DATA_DIR``（测试与便携模式重定向）；
3. 默认 ``%LOCALAPPDATA%\\WarehouseWorkbench\\data``（安装目录只存程序，不存数据库）。

每个新建的 DBAPI 连接都会执行：
``PRAGMA foreign_keys=ON``、``PRAGMA journal_mode=WAL``、``PRAGMA busy_timeout=5000``。
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

#: 应用数据目录名与数据子目录名（本地库唯一数据库文件所在处）
APP_DIR_NAME = "WarehouseWorkbench"
DATA_SUBDIR_NAME = "data"

#: 本地库数据库文件名
DB_FILE_NAME = "warehouse.db"

#: 数据目录重定向环境变量（测试与便携模式）
DATA_DIR_ENV = "WORKBENCH_DATA_DIR"

#: 写锁竞争等待上限（毫秒）
BUSY_TIMEOUT_MS = 5000


def default_data_dir() -> Path:
    """默认数据目录：%LOCALAPPDATA%\\WarehouseWorkbench\\data。"""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        # 非 Windows 或未设置 LOCALAPPDATA 时退化为用户主目录
        base = Path.home()
    return base / APP_DIR_NAME / DATA_SUBDIR_NAME


def resolve_data_dir(data_dir: Path | None = None) -> Path:
    """按“显式参数 > WORKBENCH_DATA_DIR > 默认目录”解析数据目录。"""
    if data_dir is not None:
        return data_dir
    env_dir = os.environ.get(DATA_DIR_ENV)
    if env_dir:
        return Path(env_dir)
    return default_data_dir()


def database_url(data_dir: Path) -> str:
    """数据目录对应的 SQLite URL（路径统一为 POSIX 风格，Windows 盘符可被正确解析）。"""
    resolved = data_dir.resolve()
    return f"sqlite+pysqlite:///{(resolved / DB_FILE_NAME).as_posix()}"


def create_db_engine(data_dir: Path | None = None) -> Engine:
    """创建本地库引擎：确保数据目录存在（含父目录）并挂载 PRAGMA 事件。"""
    resolved = resolve_data_dir(data_dir)
    resolved.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url(resolved))
    _install_pragmas(engine)
    return engine


def _install_pragmas(engine: Engine) -> None:
    """在每个新 DBAPI 连接上执行本地库必备 PRAGMA（§35.3）。"""

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cursor.close()


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """基于引擎返回 sessionmaker（单主工作台通过同一会话工厂串行化写入）。"""
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def connect(data_dir: Path | None = None) -> tuple[Engine, sessionmaker[Session]]:
    """便捷入口：返回 (engine, sessionmaker)，数据目录不存在则自动创建（含父目录）。"""
    engine = create_db_engine(data_dir)
    return engine, get_session_factory(engine)
