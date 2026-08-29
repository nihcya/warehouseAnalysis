"""local-data 测试配置。

所有测试通过 tmp_path 重定向数据目录，绝不触碰真实 %LOCALAPPDATA%。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from local_data.connection import connect, database_url
from sqlalchemy.orm import Session, sessionmaker

#: local-data 包根目录（alembic.ini 所在处）
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = PACKAGE_ROOT / "alembic.ini"
ALEMBIC_SCRIPT_DIR = PACKAGE_ROOT / "alembic"


def make_alembic_config(db_url: str) -> Config:
    """构造编程调用的 Alembic Config：script_location 与 URL 指向测试库。"""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_SCRIPT_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """隔离的数据目录（tmp_path 下的子目录，同时验证自动创建场景）。"""
    return tmp_path / "wb-data"


@pytest.fixture
def alembic_cfg(data_dir: Path) -> Config:
    """指向 tmp 库的 Alembic 配置（编程调用 command API）。"""
    return make_alembic_config(database_url(data_dir))


@pytest.fixture
def session_factory(data_dir: Path, alembic_cfg: Config) -> Iterator[sessionmaker[Session]]:
    """迁移到 head 后的 sessionmaker（Repository 测试用），结束后释放引擎。"""
    command.upgrade(alembic_cfg, "head")
    engine, factory = connect(data_dir)
    try:
        yield factory
    finally:
        engine.dispose()
