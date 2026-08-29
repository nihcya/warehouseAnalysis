"""workbench-desktop 测试配置。

- PySide6 以 offscreen 平台运行（无头环境可跑 UI 测试）；
- 将 ``app`` 目录以顶层名 ``workbench`` 注册进 ``sys.modules``：
  本仓库 control-plane 与本应用的源码根均为顶层 ``app`` 包，且会在同一次
  全量 pytest 进程中被收集，别名注册可避免两个同名包冲突
  （包内模块一律相对导入，两种加载名下均可工作）；
- 数据目录一律重定向到 tmp（显式 data_dir 参数），绝不触碰真实 %LOCALAPPDATA%。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from alembic import command
from alembic.config import Config
from local_data.connection import connect, database_url
from sqlalchemy.orm import Session, sessionmaker

#: workbench-desktop 包根（apps/workbench-desktop）
APP_ROOT = Path(__file__).resolve().parents[1]

#: 仓库根
REPO_ROOT = APP_ROOT.parents[1]

#: local-data 包根（alembic 脚本所在处）
LOCAL_DATA_DIR = REPO_ROOT / "local-data"

#: FakeEngine 冻结 fixture
FAKE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "fake-analysis.json"

#: golden 输入（用例默认数据源）
GOLDEN_INPUT = REPO_ROOT / "tests" / "fixtures" / "golden" / "v0.1.0" / "input.json"

#: 边界 fixture 目录（校验失败场景）
EDGE_DIR = REPO_ROOT / "tests" / "fixtures" / "edge"

#: app 包的别名顶层名（避免与 control-plane 的顶层 app 冲突）
WORKBENCH_ALIAS = "workbench"


def _register_workbench_package() -> None:
    """把 app 目录注册为顶层包 ``workbench``（幂等）。"""
    if WORKBENCH_ALIAS in sys.modules:
        return
    app_dir = APP_ROOT / "app"
    spec = importlib.util.spec_from_file_location(
        WORKBENCH_ALIAS,
        app_dir / "__init__.py",
        submodule_search_locations=[str(app_dir)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[WORKBENCH_ALIAS] = module
    spec.loader.exec_module(module)


_register_workbench_package()

from workbench.infrastructure.db.result_store import SqlResultStore
from workbench.infrastructure.engine_adapter.providers import FakeEngineProvider


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """隔离的数据目录（tmp 下子目录，同时验证自动创建场景）。"""
    return tmp_path / "wb-data"


@pytest.fixture
def session_factory(data_dir: Path) -> Iterator[sessionmaker[Session]]:
    """Alembic 迁移到 head 后的 sessionmaker（与 local-data 测试同模式）。"""
    cfg = Config()
    cfg.set_main_option("script_location", str(LOCAL_DATA_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url(data_dir))
    command.upgrade(cfg, "head")
    engine, factory = connect(data_dir)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def store(session_factory: sessionmaker[Session]) -> SqlResultStore:
    """接好本地库的结果存储（infrastructure 适配层）。"""
    return SqlResultStore(session_factory)


@pytest.fixture
def fake_provider() -> FakeEngineProvider:
    """FakeEngine 提供方（fixture 冻结结果）。"""
    return FakeEngineProvider(FAKE_FIXTURE)


@pytest.fixture
def golden_input() -> Path:
    """golden 输入路径。"""
    return GOLDEN_INPUT


@pytest.fixture
def blocking_input() -> Path:
    """触发阻断校验（DUPLICATE_EVENT）的输入路径。"""
    return EDGE_DIR / "duplicate-events.json"
