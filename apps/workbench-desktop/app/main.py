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
from .domain.benchmark_provider import BenchmarkProvider
from .domain.dataset_provider import DatasetProvider
from .domain.engine_provider import EngineProvider
from .domain.result_store import ResultStore
from .infrastructure.api_client.client import OfflineApiClient
from .infrastructure.api_client.http_client import HttpApiClient
from .infrastructure.api_client.token_store import TokenStore
from .infrastructure.backup.backup_service import BackupService
from .infrastructure.benchmark.benchmark_loader import JsonBenchmarkProvider
from .infrastructure.db.dataset_adapter import SqliteDatasetAdapter
from .infrastructure.db.result_store import SqlResultStore
from .infrastructure.engine_adapter.providers import FakeEngineProvider, LocalEngineProvider
from .infrastructure.report.exporter import ReportExporter
from .presentation.main_window import MainWindow
from .workers.status_stream_worker import StatusStreamWorker

#: 引擎选择环境变量（fake | local，缺省 local，仅组合根读取）
ENGINE_ENV = "WORKBENCH_ENGINE"

#: 引擎缺省实现：B 侧 engine 0.3.0 已交付五类公式（PR #14），故默认走真实引擎
DEFAULT_ENGINE = "local"

#: 数据源选择环境变量（local | fixture，缺省 local，仅组合根读取）
DATASET_ENV = "WORKBENCH_DATASET"

#: 仓库根（fixture 与 local-data 迁移脚本的定位基准）
REPO_ROOT = Path(__file__).resolve().parents[3]

#: FakeEngine 冻结 fixture（B 侧交付，docs/m0-handover-b.md）
FAKE_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "fake-analysis.json"

#: 基准数据集路径环境变量（缺省为 B 侧随引擎交付的 v0.1.0 fixture，Issue #10）
BENCHMARK_PATH_ENV = "WORKBENCH_BENCHMARK_PATH"

#: 基准匹配行业环境变量（缺省与 v0.1.0 fixture 的 industry 对齐）
BENCHMARK_INDUSTRY_ENV = "WORKBENCH_BENCHMARK_INDUSTRY"

#: 基准匹配区域环境变量（缺省与 v0.1.0 fixture 的 region 对齐）
BENCHMARK_REGION_ENV = "WORKBENCH_BENCHMARK_REGION"

#: 默认基准数据集（B 侧交付，F-BM-001 专用）
DEFAULT_BENCHMARK_PATH = REPO_ROOT / "tests" / "fixtures" / "benchmarks" / "v0.1.0.json"

#: 默认基准匹配行业
DEFAULT_BENCHMARK_INDUSTRY = "综合零售"

#: 默认基准匹配区域
DEFAULT_BENCHMARK_REGION = "全国"

#: local-data 包根（Alembic 迁移脚本所在处）
LOCAL_DATA_DIR = REPO_ROOT / "local-data"

#: 控制平面服务地址环境变量（默认 http://localhost:8000）
API_URL_ENV = "WORKBENCH_API_URL"

#: 默认控制平面地址
DEFAULT_API_URL = "http://localhost:8000"

#: 离线模式选择环境变量（=1 时使用离线占位客户端，用于测试场景）
OFFLINE_ENV = "WORKBENCH_OFFLINE"

#: SSE 后台线程停止等待上限（毫秒）
WORKER_STOP_TIMEOUT_MS = 5000


def create_engine_provider() -> EngineProvider:
    """按 ``WORKBENCH_ENGINE`` 选择引擎提供方；该分支只存在于组合根。

    B 侧 engine 0.3.0（PR #14）已交付五类公式共 18 个指标，故缺省由
    ``fake`` 改为 ``local``；仍需冻结结果联调时显式设 ``WORKBENCH_ENGINE=fake``。
    """
    kind = os.environ.get(ENGINE_ENV, DEFAULT_ENGINE).strip().lower()
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


def create_benchmark_provider() -> BenchmarkProvider:
    """按环境变量构造基准数据提供方（Issue #10，仅组合根读取）。

    引擎的 F-BM-001 不读文件、不访问网络，基准记录须由调用方经
    ``request.parameters["benchmarks"]`` 注入，故在此完成加载。

    文件缺失或内容非法时 ``JsonBenchmarkProvider`` 退化为空参数，
    引擎发出 ``BENCHMARK_UNAVAILABLE`` 非阻断告警，不阻断分析主流程。
    """
    raw_path = os.environ.get(BENCHMARK_PATH_ENV, "").strip()
    path = Path(raw_path) if raw_path else DEFAULT_BENCHMARK_PATH
    industry = os.environ.get(BENCHMARK_INDUSTRY_ENV, DEFAULT_BENCHMARK_INDUSTRY).strip()
    region = os.environ.get(BENCHMARK_REGION_ENV, DEFAULT_BENCHMARK_REGION).strip()
    return JsonBenchmarkProvider(path, industry=industry, region=region)


def _seed_default_warehouse(session_factory: sessionmaker[Session]) -> None:
    """迁移完成后预置 WH-01 基础仓库（幂等）。

    事件导入的存在性校验依赖 warehouse 表至少有 WH-01；空库迁移后
    自动种子，避免首次导入库存事件 CSV 时每行 WAREHOUSE_NOT_FOUND。
    """
    from local_data.repository import MasterDataRepository

    repo = MasterDataRepository(session_factory)
    if repo.get_warehouse_by_warehouse_id("WH-01") is None:
        repo.add_warehouse(warehouse_id="WH-01", name="主仓")


def create_session_factory(data_dir: Path | None = None) -> sessionmaker[Session]:
    """连接本地 SQLite（先 Alembic 迁移到 head），返回共享会话工厂。

    迁移完成后自动种子 WH-01 基础仓库（幂等），保证空库首启即可导入事件。
    """
    from alembic import command
    from alembic.config import Config
    from local_data.connection import connect, database_url, resolve_data_dir

    resolved = resolve_data_dir(data_dir)
    cfg = Config()
    cfg.set_main_option("script_location", str(LOCAL_DATA_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url(resolved))
    command.upgrade(cfg, "head")
    _engine, session_factory = connect(resolved)
    _seed_default_warehouse(session_factory)
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


def create_api_client() -> HttpApiClient | OfflineApiClient:
    """按环境变量组装控制平面客户端（组合根分支）。

    ``WORKBENCH_OFFLINE=1`` 时返回离线占位客户端（测试场景）；否则读取
    ``WORKBENCH_API_URL``（默认 ``http://localhost:8000``）创建 ``HttpApiClient``，
    令牌持久化使用与本地库相同的数据目录逻辑（尊重 ``WORKBENCH_DATA_DIR``，
    便于测试隔离）。
    """
    if os.environ.get(OFFLINE_ENV, "").strip() == "1":
        return OfflineApiClient()
    base_url = os.environ.get(API_URL_ENV, DEFAULT_API_URL).strip() or DEFAULT_API_URL
    from local_data.connection import resolve_data_dir

    token_store = TokenStore(data_dir=resolve_data_dir(None))
    return HttpApiClient(base_url=base_url, token_store=token_store)


def main() -> int:
    """组装依赖并启动 PySide6 主窗口。"""
    app = QApplication(sys.argv)
    engine = create_engine_provider()
    session_factory = create_session_factory()
    store = SqlResultStore(session_factory)
    dataset_provider = create_dataset_provider(session_factory)
    benchmark_provider = create_benchmark_provider()
    use_case = RunAnalysisUseCase(
        engine,
        store,
        dataset_provider=dataset_provider,
        benchmark_provider=benchmark_provider,
    )
    import_manager = CsvImportManager(session_factory)
    report_manager = create_report_export_manager(session_factory)
    backup_manager = create_backup_manager(session_factory)
    api_client = create_api_client()
    # SSE 状态流后台线程（登录成功后启动，信号已连入 StatusCard）
    status_worker = StatusStreamWorker(api_client)
    window = MainWindow(
        use_case,
        store,
        api_client=api_client,
        import_manager=import_manager,
        report_manager=report_manager,
        backup_manager=backup_manager,
        status_worker=status_worker,
    )
    # 离线占位客户端（测试场景）直接进入离线模式，跳过登录检查与 SSE
    if isinstance(api_client, HttpApiClient):
        # 登录检查：有效令牌静默放行，否则弹出登录对话框
        logged_in = window.check_login_on_startup(api_client)
        if logged_in:
            # 登录成功：自动注册设备 + 刷新状态 + 启动 SSE 后台线程
            window.auto_register_device(api_client)
            window.refresh_status(api_client)
            status_worker.start()
        # 登录取消/控制平面不可达 → 离线模式（状态栏已明示“离线”），不阻断本地操作
    window.show()
    exit_code = app.exec()
    # 窗口关闭后停止 SSE 后台线程
    if status_worker.isRunning():
        status_worker.stop()
        status_worker.wait(WORKER_STOP_TIMEOUT_MS)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
