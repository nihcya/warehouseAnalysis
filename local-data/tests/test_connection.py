"""连接工厂测试：PRAGMA 三项、目录自动创建、数据目录重定向。"""

from __future__ import annotations

from pathlib import Path

from local_data.connection import connect, database_url, default_data_dir
from sqlalchemy import text

LOCAL_TABLES = {"local_meta", "analysis_run", "analysis_result"}


def test_connection_pragmas(tmp_path: Path) -> None:
    """每个连接：foreign_keys=1、journal_mode=wal、busy_timeout=5000（§35.3）。"""
    engine, _factory = connect(tmp_path / "data")
    try:
        with engine.connect() as conn:
            foreign_keys = conn.execute(text("PRAGMA foreign_keys")).scalar()
            journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
            busy_timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
        assert foreign_keys == 1
        assert str(journal_mode).lower() == "wal"
        assert busy_timeout == 5000
    finally:
        engine.dispose()


def test_data_dir_created_recursively(tmp_path: Path) -> None:
    """数据目录不存在（含多级父目录）时自动创建，首次连接生成 db 文件。"""
    data_dir = tmp_path / "a" / "b" / "c"
    engine, _factory = connect(data_dir)
    try:
        assert data_dir.is_dir()
        with engine.connect():
            pass
        assert (data_dir / "warehouse.db").is_file()
    finally:
        engine.dispose()


def test_env_var_redirects_data_dir(monkeypatch, tmp_path: Path) -> None:
    """WORKBENCH_DATA_DIR 重定向数据目录（测试/便携模式）。"""
    env_dir = tmp_path / "env-data"
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(env_dir))
    engine, _factory = connect()
    try:
        assert engine.url.render_as_string(hide_password=False) == database_url(env_dir)
        with engine.connect():
            pass
        assert (env_dir / "warehouse.db").is_file()
    finally:
        engine.dispose()


def test_explicit_data_dir_overrides_env(monkeypatch, tmp_path: Path) -> None:
    """显式 data_dir 参数优先于 WORKBENCH_DATA_DIR 环境变量。"""
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path / "env-data"))
    explicit_dir = tmp_path / "explicit-data"
    engine, _factory = connect(explicit_dir)
    try:
        assert engine.url.render_as_string(hide_password=False) == database_url(explicit_dir)
    finally:
        engine.dispose()


def test_default_data_dir_uses_localappdata(monkeypatch, tmp_path: Path) -> None:
    """默认目录为 %LOCALAPPDATA%\\WarehouseWorkbench\\data（仅路径推导，不落盘）。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_data_dir() == tmp_path / "WarehouseWorkbench" / "data"


def test_fresh_engine_has_no_business_tables(tmp_path: Path) -> None:
    """未跑迁移的库只有空文件，不含业务表（建表一律走 Alembic）。"""
    engine, _factory = connect(tmp_path / "data")
    try:
        with engine.connect() as conn:
            names = {
                row[0]
                for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).all()
            }
        assert not LOCAL_TABLES & names
    finally:
        engine.dispose()
