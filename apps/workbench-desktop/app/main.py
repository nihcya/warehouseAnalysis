"""组合根与启动入口（§7.2/§7.3）：依赖组装只发生在这里。

- ``WORKBENCH_ENGINE=fake|local`` 仅在本模块读取一次（``create_engine_provider``），
  presentation / application 不出现任何 ``if fake`` 分支；
- ``WORKBENCH_DATASET=local|fixture`` 同样只在本模块读取（``create_dataset_provider``）：
  本地库适配器为 M1 默认数据源，golden fixture 保留供测试/演示；
- 本地库启动时经 Alembic 迁移到 head（幂等），数据目录重定向规则见
  ``local_data.connection``（显式参数 > ``WORKBENCH_DATA_DIR`` > %LOCALAPPDATA%）；
- 启动方式（主基线 §20.3）：``cd apps/workbench-desktop && python -m app.main``。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from sqlalchemy.orm import Session, sessionmaker

from .application.analysis_usecase import (
    DEFAULT_INPUT_PATH,
    FixtureDatasetProvider,
    RunAnalysisUseCase,
)
from .application.backup_manager import BackupManager
from .application.import_manager import CsvImportManager
from .application.report_export import ReportExportManager
from .domain.dataset_provider import DatasetProvider
from .domain.engine_provider import EngineProvider
from .domain.result_store import ResultStore
from .infrastructure.api_client.client import OfflineApiClient
from .infrastructure.backup.backup_service import BackupService
from .infrastructure.db.dataset_adapter import SqliteDatasetAdapter
from .infrastructure.db.result_store import SqlResultStore
from .infrastructure.engine_adapter.providers import FakeEngineProvider, LocalEngineProvider
from .infrastructure.report.exporter import ReportExporter
from .presentation.main_window import MainWindow

#: 引擎选择环境变量（fake | local，缺省 fake，仅组合根读取）
ENGINE_ENV = "WORKBENCH_ENGINE"

#: 数据源选择环境变量（local | fixture，缺省 local，仅组合根读取）
DATASET_ENV = "WORKBENCH_DATASET"

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


def create_dataset_provider(
    session_factory: sessionmaker[Session],
) -> DatasetProvider:
    """按 ``WORKBENCH_DATASET`` 选择数据源；本地库适配器为 M1 默认。

    该分支只存在于组合根（spec MODIFIED：分析用例数据源），presentation 层
    不感知数据来源差异。
    """
    kind = os.environ.get(DATASET_ENV, "local").strip().lower()
    if kind == "fixture":
        return FixtureDatasetProvider(DEFAULT_INPUT_PATH)
    return SqliteDatasetAdapter(session_factory)


def create_session_factory(data_dir: Path | None = None) -> sessionmaker[Session]:
    """连接本地 SQLite（先 Alembic 迁移到 head），返回共享会话工厂。"""
    from alembic import command
    from alembic.config import Config
    from local_data.connection import connect, database_url, resolve_data_dir

    resolved = resolve_data_dir(data_dir)
    cfg = Config()
    cfg.set_main_option("script_location", str(LOCAL_DATA_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url(resolved))
    command.upgrade(cfg, "head")
    _engine, session_factory = connect(resolved)
    return session_factory


def create_result_store(data_dir: Path | None = None) -> ResultStore:
    """连接本地 SQLite（先迁移到 head），返回结果存储端口。"""
    return SqlResultStore(create_session_factory(data_dir))


def create_report_export_manager(
    session_factory: sessionmaker[Session],
    data_dir: Path | None = None,
) -> ReportExportManager:
    """报告导出用例：报告目录与数据目录同级（默认 %LOCALAPPDATA%\\...\\reports），
    显式 data_dir 重定向时报告目录跟随（测试隔离）。"""
    from local_data.connection import resolve_data_dir

    reports_dir = _sibling_dir(resolve_data_dir(data_dir), "reports")
    return ReportExportManager(ReportExporter(session_factory, reports_dir))


def create_backup_manager(
    session_factory: sessionmaker[Session],
    data_dir: Path | None = None,
) -> BackupManager:
    """备份管理用例：备份目录与数据目录同级（默认 %LOCALAPPDATA%\\...\\backups），
    显式 data_dir 重定向时备份目录跟随（测试隔离）。"""
    from local_data.connection import DB_FILE_NAME, resolve_data_dir

    resolved = resolve_data_dir(data_dir)
    backup_dir = _sibling_dir(resolved, "backups")
    db_path = resolved / DB_FILE_NAME
    return BackupManager(BackupService(session_factory), db_path, backup_dir)


def _sibling_dir(data_dir: Path, name: str) -> Path:
    """数据目录的同级子目录（data/… → name/…，与 local_data 目录约定一致）。"""
    return data_dir.parent / name


def main() -> int:
    """组装依赖并启动 PySide6 主窗口。"""
    app = QApplication(sys.argv)
    engine = create_engine_provider()
    session_factory = create_session_factory()
    store = SqlResultStore(session_factory)
    dataset_provider = create_dataset_provider(session_factory)
    use_case = RunAnalysisUseCase(engine, store, dataset_provider=dataset_provider)
    import_manager = CsvImportManager(session_factory)
    report_manager = create_report_export_manager(session_factory)
    backup_manager = create_backup_manager(session_factory)
    window = MainWindow(
        use_case,
        store,
        api_client=OfflineApiClient(),
        import_manager=import_manager,
        report_manager=report_manager,
        backup_manager=backup_manager,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
