"""组合根与启动入口（§7.2/§7.3）：依赖组装只发生在这里。

- ``WORKBENCH_ENGINE=fake|local`` 仅在本模块读取一次（``create_engine_provider``），
  presentation / application 不出现任何 ``if fake`` 分支；
- 本地库启动时经 Alembic 迁移到 head（幂等），数据目录重定向规则见
  ``local_data.connection``（显式参数 > ``WORKBENCH_DATA_DIR`` > %LOCALAPPDATA%）；
- 启动方式（主基线 §20.3）：``cd apps/workbench-desktop && python -m app.main``。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .application.analysis_usecase import DEFAULT_INPUT_PATH, RunAnalysisUseCase
from .domain.engine_provider import EngineProvider
from .domain.result_store import ResultStore
from .infrastructure.api_client.client import OfflineApiClient
from .infrastructure.db.result_store import SqlResultStore
from .infrastructure.engine_adapter.providers import FakeEngineProvider, LocalEngineProvider
from .presentation.main_window import MainWindow

#: 引擎选择环境变量（fake | local，缺省 fake，仅组合根读取）
ENGINE_ENV = "WORKBENCH_ENGINE"

#: 仓库根（fixture 与 local-data 迁移脚本的定位基准）
REPO_ROOT = Path(__file__).resolve().parents[3]

#: FakeEngine 冻结 fixture（B 侧交付，docs/m0-handover-b.md）
FAKE_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "fake-analysis.json"

#: local-data 包根（Alembic 迁移脚本所在处）
LOCAL_DATA_DIR = REPO_ROOT / "local-data"


def create_engine_provider() -> EngineProvider:
    """按 ``WORKBENCH_ENGINE`` 选择引擎提供方；该分支只存在于组合根。"""
    kind = os.environ.get(ENGINE_ENV, "fake").strip().lower()
    if kind == "local":
        return LocalEngineProvider()
    return FakeEngineProvider(FAKE_FIXTURE_PATH)


def create_result_store(data_dir: Path | None = None) -> ResultStore:
    """连接本地 SQLite（先迁移到 head），返回结果存储端口。"""
    from alembic import command
    from alembic.config import Config
    from local_data.connection import connect, database_url, resolve_data_dir

    resolved = resolve_data_dir(data_dir)
    cfg = Config()
    cfg.set_main_option("script_location", str(LOCAL_DATA_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url(resolved))
    command.upgrade(cfg, "head")
    _engine, session_factory = connect(resolved)
    return SqlResultStore(session_factory)


def main() -> int:
    """组装依赖并启动 PySide6 主窗口。"""
    app = QApplication(sys.argv)
    engine = create_engine_provider()
    store = create_result_store()
    use_case = RunAnalysisUseCase(engine, store, DEFAULT_INPUT_PATH)
    window = MainWindow(use_case, store, api_client=OfflineApiClient())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
