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
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from alembic import command
from alembic.config import Config
from local_data.connection import connect, database_url
from local_data.repository import MasterDataRepository, StockSnapshotRepository
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

from workbench.application.import_manager import (
    IMPORT_TYPE_EVENTS,
    IMPORT_TYPE_MASTER,
    CsvImportManager,
    ImportRunSummary,
)
from workbench.infrastructure.db.result_store import SqlResultStore
from workbench.infrastructure.engine_adapter.providers import FakeEngineProvider

#: 导入样本：主数据 CSV（2 SKU）
SAMPLE_MASTER_CSV = (
    "sku_id,name,category,unit,unit_cost\n"
    "SKU-0001,矿泉水 550ml,饮料,瓶,1.20\n"
    "SKU-0002,能量饮料 250ml,饮料,罐,3.50\n"
)

#: 导入样本：库存事件 CSV（含调拨出仓与盘点，期间 2026-06-02 ~ 2026-06-15）
SAMPLE_EVENTS_CSV = (
    "event_id,sku_id,warehouse_id,move_type,quantity,occurred_at,unit_cost\n"
    "EVT-0001,SKU-0001,WH-01,INBOUND,100,2026-06-02,1.20\n"
    "EVT-0002,SKU-0001,WH-01,OUTBOUND,30,2026-06-05,\n"
    "EVT-0003,SKU-0002,WH-01,TRANSFER_OUT,10,2026-06-12,\n"
    "EVT-0004,SKU-0002,WH-01,ADJUSTMENT,60,2026-06-15,\n"
)

#: 主数据导入字段映射（契约字段 → CSV 列名）
SAMPLE_MASTER_MAPPING = {
    "sku_id": "sku_id",
    "name": "name",
    "category": "category",
    "unit": "unit",
    "unit_cost": "unit_cost",
}

#: 事件导入字段映射
SAMPLE_EVENTS_MAPPING = {
    "event_id": "event_id",
    "sku_id": "sku_id",
    "warehouse_id": "warehouse_id",
    "move_type": "move_type",
    "quantity": "quantity",
    "occurred_at": "occurred_at",
    "unit_cost": "unit_cost",
}


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
def import_manager(session_factory: sessionmaker[Session]) -> CsvImportManager:
    """CSV 导入用例（接 tmp 重定向本地库，纯逻辑无 Qt）。"""
    return CsvImportManager(session_factory)


@pytest.fixture
def fake_provider() -> FakeEngineProvider:
    """FakeEngine 提供方（fixture 冻结结果）。"""
    return FakeEngineProvider(FAKE_FIXTURE)


@pytest.fixture
def seeded_local_db(session_factory: sessionmaker[Session], tmp_path: Path):
    """导入样本数据后的本地库（本地适配器/分析闭环测试的公共数据源）。

    内容：WH-01 仓库 + 2 SKU + 4 事件（CSV 导入，含调拨出仓与盘点）
    + 1 条快照（2026-06-30，直接经仓储写入）；导入全程走 CsvImportManager，
    覆盖“导入 → 适配”闭环中的导入侧。
    """
    MasterDataRepository(session_factory).add_warehouse(warehouse_id="WH-01", name="主仓")
    manager = CsvImportManager(session_factory)

    master_csv = tmp_path / "sample-master.csv"
    master_csv.write_text(SAMPLE_MASTER_CSV, encoding="utf-8")
    master_summary = manager.run_import(
        path=master_csv,
        import_type=IMPORT_TYPE_MASTER,
        mapping=SAMPLE_MASTER_MAPPING,
    )
    assert isinstance(master_summary, ImportRunSummary) and master_summary.completed

    events_csv = tmp_path / "sample-events.csv"
    events_csv.write_text(SAMPLE_EVENTS_CSV, encoding="utf-8")
    events_summary = manager.run_import(
        path=events_csv,
        import_type=IMPORT_TYPE_EVENTS,
        mapping=SAMPLE_EVENTS_MAPPING,
    )
    assert isinstance(events_summary, ImportRunSummary) and events_summary.completed

    StockSnapshotRepository(session_factory).save_snapshot(
        snapshot_id="SNAP-0001",
        snapshot_date="2026-06-30",
        sku_id="SKU-0001",
        warehouse_id="WH-01",
        quantity=Decimal(70),
        inventory_value=Decimal("84.00"),
    )
    return session_factory


@pytest.fixture
def golden_input() -> Path:
    """golden 输入路径。"""
    return GOLDEN_INPUT


@pytest.fixture
def blocking_input() -> Path:
    """触发阻断校验（DUPLICATE_EVENT）的输入路径。"""
    return EDGE_DIR / "duplicate-events.json"
