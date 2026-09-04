"""组合根与启动入口（§7.2/§7.3）：依赖组装只发生在这里。

- ``WORKBENCH_ENGINE=fake|local`` 仅在本模块读取一次（``create_engine_provider``），
  presentation / application 不出现任何 ``if fake`` 分支；
- ``WORKBENCH_DATASET=local|fixture`` 同样只在本模块读取（``create_dataset_provider``）：
  本地库适配器为 M1 默认数据源，golden fixture 保留供测试/演示；
- 本地库启动时经 Alembic 迁移到 head（幂等），数据目录重定向规则见
  ``local_data.connection``（显式参数 > ``WORKBENCH_DATA_DIR`` > %LOCALAPPDATA%）；
- 升级安全（M3 Task 5）：迁移（升级）前先对旧库做一次 AUTO 快照备份；
  迁移失败不触碰库文件，进入安全模式（``SafeModeDialog``：展示错误摘要、
  从备份恢复、只读继续、退出）。恢复成功后**就地重试迁移**——重试成功
  直接进入主窗口（无需重启），仍失败可再次恢复/只读继续/退出；用户选择
  "安全模式继续（只读）"时以 ``safe_mode`` 标志进入主窗口，导入/分析
  入口禁用（无任何业务写入）；安全模式下不执行任何业务查询/写入；
- 每日自动备份由 ``AutoBackupScheduler``（QTimer 主线程）驱动；
- 启动方式（主基线 §20.3）：``cd apps/workbench-desktop && python -m app.main``。
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from sqlalchemy.orm import Session, sessionmaker

from .agent.agent_worker import AgentWorker
from .application.analysis_usecase import (
    DEFAULT_INPUT_PATH,
    FixtureDatasetProvider,
    RunAnalysisUseCase,
)
from .application.auto_backup_scheduler import AutoBackupScheduler
from .application.backup_manager import BackupManager
from .application.import_manager import CsvImportManager
from .application.report_export import ReportExportManager
from .application.upgrade_safety import (
    PreUpgradeBackupStatus,
    run_pre_upgrade_backup,
    version_changed_since_last_run,
    write_last_run_version,
)
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
from .presentation.main_window import APP_VERSION, MainWindow
from .presentation.safe_mode_dialog import SafeModeDialog
from .presentation.tray_icon import TrayIcon
from .workers.status_stream_worker import StatusStreamWorker
from .workers.sync_worker import SyncWorker

#: 引擎选择环境变量（fake | local，缺省 local，仅组合根读取）
ENGINE_ENV = "WORKBENCH_ENGINE"

#: 引擎缺省实现：B 侧 engine 0.3.0 已交付五类公式（PR #14），故默认走真实引擎
DEFAULT_ENGINE = "local"

#: 数据源选择环境变量（local | fixture，缺省 local，仅组合根读取）
DATASET_ENV = "WORKBENCH_DATASET"

#: 仓库根（fixture 与 local-data 迁移脚本的定位基准）：
#: 源码运行 = ``apps/workbench-desktop`` 的上三级；PyInstaller ``--onedir``
#: 冻结后 = ``_internal``（workbench.spec 已把 local-data 与 tests/fixtures
#: 作为 datas 收集到该目录，见 dist 布局约定）
if getattr(sys, "frozen", False):
    REPO_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent / "_internal"))
else:
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

#: 每日自动备份时刻（本地时间小时，透传给调度器）
AUTO_BACKUP_HOUR = 3

#: 安全模式退出码（区别于正常退出的 0，提示用户重启应用重试迁移）
SAFE_MODE_EXIT_CODE = 3

#: 组合根日志（升级前备份等启动期事件）
logger = logging.getLogger(__name__)


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

    升级安全（M3 Task 5）：迁移（升级）前若旧库已存在，先用纯 sqlite3
    ``VACUUM INTO`` 做一次 AUTO 快照备份（备份目录与数据目录同级），成功
    后才开始 ``upgrade head``；备份失败视为环境异常、不启动迁移。随后经
    ``prepare_upgrade_backup``（BackupManager）再打一份带版本号标记的
    登记备份（版本变化时才打，同版本启动不重复全量快照）。迁移失败时
    旧库文件零改动，由调用方捕获异常进入安全模式。
    迁移完成后自动种子 WH-01 基础仓库（幂等），保证空库首启即可导入事件。
    """
    from alembic import command
    from alembic.config import Config
    from local_data.connection import DB_FILE_NAME, connect, database_url, resolve_data_dir

    resolved = resolve_data_dir(data_dir)
    db_path = resolved / DB_FILE_NAME
    if db_path.exists():
        backup_pre_upgrade_snapshot(db_path, resolved)
        prepare_upgrade_backup(db_path, resolved)
    cfg = Config()
    cfg.set_main_option("script_location", str(LOCAL_DATA_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url(resolved))
    command.upgrade(cfg, "head")
    _engine, session_factory = connect(resolved)
    _seed_default_warehouse(session_factory)
    write_last_run_version(resolved, APP_VERSION)
    return session_factory


def prepare_upgrade_backup(
    db_path: Path,
    data_dir: Path,
    app_version: str = APP_VERSION,
) -> object:
    """升级（迁移）前备份（M3 Task 5，SubTask 5.2）：版本号写入备份备注。

    经 ``BackupManager``（``BackupService`` VACUUM INTO + backup_record 登记，
    AUTO 类型）对当前库做一次全量备份，应用版本写入结果消息文本（备份页
    可见，作为该备份"由哪个版本升级前打出"的备注标记）。仅当版本相对
    上次运行发生变化（``app_meta.json`` 门控）才真正执行，同版本每次启动
    不重复全量快照；失败仅记日志不阻断迁移（迁移前另有零依赖快照保底）。

    :return: ``PreUpgradeBackupStatus``（未触发时 ``ok=False`` 且 message
        说明跳过原因，鸭子类型便于测试替身）。
    """
    from local_data.connection import connect
    from local_data.connection import resolve_data_dir as _resolve

    resolved = _resolve(data_dir)
    if not version_changed_since_last_run(resolved, app_version):
        logger.info("版本未变化（v%s），跳过升级前登记备份", app_version)
        return PreUpgradeBackupStatus(ok=False, message="版本未变化，跳过升级前备份")
    if not _table_exists(db_path, "backup_record"):
        # 旧库可能缺登记表（迁移前 schema 未就绪）：登记备份依赖该表，直接
        # 跳过且绝不打开 SQLAlchemy 连接——连接层 PRAGMA 会改库头（WAL），
        # 破坏"迁移失败库文件零改动"不变量；零依赖快照已保底。
        logger.warning("登记表 backup_record 不存在，跳过升级前登记备份（不阻断迁移）")
        return PreUpgradeBackupStatus(
            ok=False, message="backup_record 表不存在，跳过升级前登记备份"
        )
    backup_dir = _sibling_dir(resolved, "backups")
    try:
        service = BackupService(connect(resolved)[1])
        manager = BackupManager(service, db_path, backup_dir)
        status = run_pre_upgrade_backup(service, db_path, backup_dir, app_version)
    except Exception as exc:  # noqa: BLE001 —— 登记备份失败不阻断迁移
        # 升级前登记备份失败不阻断迁移：零依赖快照（backup_pre_upgrade_snapshot）
        # 已成功，迁移安全性不受影响；此处仅记录原因。
        logger.warning("升级前登记备份失败（不阻断迁移）：%s", exc)
        return PreUpgradeBackupStatus(ok=False, message=str(exc))
    if status.ok:
        logger.info("%s（%s）", status.message, manager.backup_dir)
    else:
        logger.warning("升级前登记备份失败（不阻断迁移）：%s", status.message)
    return status


def _table_exists(db_path: Path, table: str) -> bool:
    """只读检查库中是否存在某表（标准库 sqlite3，不触发连接层 WAL PRAGMA）。"""
    import sqlite3
    from contextlib import closing

    if not db_path.exists():
        return False
    try:
        with closing(
            sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        ) as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def backup_pre_upgrade_snapshot(db_path: Path, data_dir: Path) -> Path:
    """升级（迁移）前备份：旧库的 VACUUM INTO 全量快照（M3 Task 5）。

    刻意不经 ``BackupService``：安全模式场景下业务库（含 backup_record
    登记表）可能不可用，登记必须零依赖；此处为迁移/库损坏失败路径保底，
    只用标准库 sqlite3 完成。备份生成后先 integrity_check 复验再放行迁移。
    失败抛出异常由 ``create_session_factory`` 上抛（不启动迁移即不触碰
    库文件，维持零改动不变量）。

    :return: 生成的备份文件路径（安全模式据此引导恢复）。
    """
    import sqlite3
    import uuid
    from contextlib import closing
    from datetime import UTC, datetime

    backup_dir = data_dir.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"backup-{stamp}-{uuid.uuid4().hex[:12]}.db"
    # VACUUM INTO 不支持参数绑定，单引号按 SQL 字面量转义
    quoted = str(backup_path).replace("'", "''")
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(f"VACUUM INTO '{quoted}'")
    except sqlite3.Error as exc:
        _discard_snapshot(backup_path)
        raise RuntimeError(f"升级前备份失败，已中止迁移：{exc}") from exc
    if not _snapshot_integrity_ok(backup_path):
        _discard_snapshot(backup_path)
        raise RuntimeError("升级前备份完整性校验失败（integrity_check），已中止迁移")
    logger.info("升级前备份完成：%s", backup_path)
    return backup_path


def _discard_snapshot(backup_path: Path) -> None:
    """删除失败的升级前备份快照（清理失败不掩盖原错误）。"""
    try:
        backup_path.unlink(missing_ok=True)
    except OSError:
        pass


def _snapshot_integrity_ok(path: Path) -> bool:
    """以只读连接打开快照并执行 integrity_check（不写被检文件）。"""
    import sqlite3
    from contextlib import closing

    try:
        with closing(
            sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        ) as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error:
        return False
    return row is not None and str(row[0]).lower() == "ok"


def create_readonly_session_factory(data_dir: Path | None = None) -> sessionmaker[Session]:
    """只读安全模式的会话工厂：**跳过迁移**、不写种子数据/版本标记。

    用户在安全模式选择"只读继续"时使用：迁移失败意味着库 schema 与
    当前代码不匹配，此时重跑迁移已被证明失败，直接 ``connect`` 打开
    现有库供只读浏览；任何写入（种子/版本标记）都属改变库状态，违背
    安全模式"无任何业务写入"承诺。
    """
    from local_data.connection import DB_FILE_NAME, connect, resolve_data_dir

    resolved = resolve_data_dir(data_dir)
    if not (resolved / DB_FILE_NAME).exists():
        raise RuntimeError("本地库文件不存在，无法只读继续")
    _engine, session_factory = connect(resolved)
    return session_factory


def _run_safe_mode(error: BaseException, data_dir: Path | None = None) -> bool:
    """安全模式：弹出 SafeModeDialog（错误摘要/备份恢复/只读继续/退出），阻塞到关闭。

    安全模式不做任何业务组装（不建 use_case/业务连接），仅透传恢复回调；
    恢复成功提示用户重启（重启会重放本次升级前的快照备份链路）。

    :return: 用户选择"安全模式继续（只读）"返回 ``True``，组合根据此以
        ``safe_mode`` 标志进入只读主窗口；恢复成功或退出均返回 ``False``
        （调用方以 :data:`SAFE_MODE_EXIT_CODE` 结束进程，提示重启重试）。
    """
    from local_data.connection import DB_FILE_NAME, resolve_data_dir

    resolved = resolve_data_dir(data_dir)
    db_path = resolved / DB_FILE_NAME
    backup_dir = _sibling_dir(resolved, "backups")
    restore = _build_safe_mode_restore()
    dialog = SafeModeDialog(error, db_path, backup_dir, restore=restore)
    dialog.exec()
    return bool(getattr(dialog, "continue_readonly", False))


def _build_safe_mode_restore() -> Callable[[Path, Path], object]:
    """组装安全模式恢复回调：``BackupService.restore``（先校验后原子替换）。

    迁移失败场景下业务库不可用，无法登记 backup_record——``BackupService``
    按文件路径查不到登记（``record=None``）时自动跳过 SHA-256 比对，改在
    恢复前的临时副本上做 integrity / 版本 / 关键表校验，满足"未登记文件
    也可安全恢复"；``safety_backup_dir=None`` 跳过"恢复前安全备份"（该
    步骤要登记进业务库，安全模式下链路不完整）。
    """
    from local_data.connection import connect

    def restore(backup_path: Path, db_path: Path) -> object:
        _engine, session_factory = connect(db_path.parent)
        service = BackupService(session_factory)
        return service.restore(backup_path, db_path, safety_backup_dir=None)

    return restore


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


def _noop_backup() -> object:
    """安全模式下自动备份调度器的占位回调：不做任何备份。"""

    class _Skipped:
        ok = False

        def __init__(self) -> None:
            self.message = "安全模式（只读）：自动备份已停用"

    return _Skipped()


def _notify_backup_finished(tray: TrayIcon) -> Callable[[bool, str], None]:
    """把自动备份结果转成托盘气泡（成功 Information / 失败 Warning）。"""

    def handler(ok: bool, message: str) -> None:
        icon = (
            QSystemTrayIcon.MessageIcon.Information
            if ok
            else QSystemTrayIcon.MessageIcon.Warning
        )
        tray.showMessage("自动备份", message, icon, 5000)

    return handler


def main() -> int:
    """组装依赖并启动 PySide6 主窗口。"""

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    engine = create_engine_provider()
    safe_mode = False
    try:
        session_factory = create_session_factory()
    except Exception as exc:  # noqa: BLE001 —— 升级/备份失败进入安全模式
        # 安全模式：不组装任何业务依赖（库不可用），只提供备份恢复、
        # 只读继续与退出。恢复成功后就地重试迁移：重试成功直接进入主窗口
        # （无需重启），仍失败回到对话框（可再次恢复/只读继续/退出）。
        logger.error("本地库启动失败，进入安全模式：%s", exc)
        session_factory = None
        while True:
            try:
                session_factory = create_session_factory()
            except Exception as retry_exc:  # noqa: BLE001 —— 重试仍失败回安全模式
                logger.error("安全模式迁移重试仍失败：%s", retry_exc)
                if not _run_safe_mode(retry_exc):
                    return SAFE_MODE_EXIT_CODE
                # 只读继续：跳过迁移直接打开现有库（任何写操作均被主窗口
                # safe_mode 禁用）；连只读连接都建不起来就只能退出。
                try:
                    session_factory = create_readonly_session_factory()
                except Exception as ro_exc:  # noqa: BLE001 —— 只读也失败则退出
                    logger.error("只读模式打开本地库失败，退出：%s", ro_exc)
                    return SAFE_MODE_EXIT_CODE
                safe_mode = True
                break
            break
    # 上述分支必已为 session_factory 赋值（成功 / 重试成功 / 只读继续）
    assert session_factory is not None
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
    # 安全模式（只读）下 ReportPage 构造期会查询 analysis_run（旧库可能缺表），
    # 与 backup_manager 同理传 None 阻止报告页构建，规避对旧库的业务查询。
    report_manager = None if safe_mode else create_report_export_manager(session_factory)
    # 安全模式（只读）下库 schema 可能缺 backup_record 表，BackupPage 构造期
    # 会查询备份列表 → 传 None 阻止备份页构建，规避对旧库的业务查询。
    backup_manager = None if safe_mode else create_backup_manager(session_factory)
    api_client = create_api_client()
    # SSE 状态流后台线程（登录成功后启动，信号已连入 StatusCard）
    status_worker = StatusStreamWorker(api_client)
    # 同步后台线程（M3 Task 2/4：拉取云端信封；登录注册成功后设定设备再启动）
    sync_worker = SyncWorker(api_client, session_factory, device_id="")
    # Agent 后台线程（M3 Task 3：心跳 / 配置 / 任务；登录注册成功后启动）
    # pending_sync_provider：心跳载荷携带本地待同步数（SyncWorker 落库视图）
    agent_worker = AgentWorker(
        api_client,
        device_id="",
        app_version=APP_VERSION,
        engine_version=engine.engine_version,
        pending_sync_provider=sync_worker.pending_count,
    )
    window = MainWindow(
        use_case,
        store,
        api_client=api_client,
        import_manager=import_manager,
        report_manager=report_manager,
        backup_manager=backup_manager,
        status_worker=status_worker,
        agent_worker=agent_worker,
        sync_worker=sync_worker,
        safe_mode=safe_mode,
    )
    # 系统托盘：窗口关闭驻留后台，托盘"退出"先停后台线程再退出
    tray = TrayIcon(window)
    agent_worker.config_rejected.connect(tray.config_rejected)
    tray.show()
    # 每日自动备份调度（M3 Task 5）：QTimer 主线程驱动，结果经托盘气泡提示；
    # 安全模式下 backup_manager 为 None，自动备份同步停用（备份页同样被禁）。
    scheduler = AutoBackupScheduler(
        (backup_manager.run_manual_backup if backup_manager else _noop_backup),
        backup_hour=AUTO_BACKUP_HOUR,
        parent=app,
    )
    scheduler.backup_finished.connect(_notify_backup_finished(tray))
    scheduler.start()
    # 离线占位客户端（测试场景）直接进入离线模式，跳过登录检查与 SSE
    if isinstance(api_client, HttpApiClient):
        # 登录检查：有效令牌静默放行，否则弹出登录对话框
        logged_in = window.check_login_on_startup(api_client)
        if logged_in:
            # 登录成功：自动注册设备 + 刷新状态 + 启动 SSE 后台线程
            device_id = window.auto_register_device(api_client)
            window.refresh_status(api_client)
            status_worker.start()
            if device_id:
                agent_worker.set_device_id(device_id)
                agent_worker.start()
                sync_worker.set_device_id(device_id)
                sync_worker.start()
        # 登录取消/控制平面不可达 → 离线模式（状态栏已明示"离线"），不阻断本地操作
    window.show()
    exit_code = app.exec()
    # 应用退出后兜底停止后台线程（正常路径由托盘"退出"先停线程）
    scheduler.stop()
    if sync_worker.isRunning():
        sync_worker.stop()
        sync_worker.wait(WORKER_STOP_TIMEOUT_MS)
    if status_worker.isRunning():
        status_worker.stop()
        status_worker.wait(WORKER_STOP_TIMEOUT_MS)
    if agent_worker.isRunning():
        agent_worker.stop()
        agent_worker.wait(WORKER_STOP_TIMEOUT_MS)
    tray.hide()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
