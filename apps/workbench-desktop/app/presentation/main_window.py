"""主窗口（§7.2）：左侧导航占位 + 顶部状态栏占位 + 页面容器。

- 导航条目：总览/货品/库存流水/导入/分析/调度/报告/备份/设置
  （M0 仅"分析"实装；M1 增加"导入"（CSV 导入向导入口页）与
  "报告"（报告导出页）/"备份"（备份恢复页），替换 M0 占位）；
- 状态栏：网络/授权/待同步/版本 占位（网络状态来自注入的 ApiClient 端口）；
- 安全模式只读（M3 Task 5）：``safe_mode=True`` 时导入与分析入口禁用
  （``ImportPage.set_read_only`` / ``AnalysisPage`` 禁用"运行分析"按钮），
  仅保留浏览类功能，不做任何业务写入；
- 依赖全部通过构造函数注入（组合根 ``app.main`` 组装），本模块不 import infrastructure。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..application.analysis_usecase import RunAnalysisUseCase
from ..application.backup_manager import BackupManager
from ..application.import_manager import CsvImportManager
from ..application.report_export import ReportExportManager
from ..domain.api_client import ApiClient
from ..domain.result_store import ResultStore
from .analysis_page import AnalysisPage
from .backup_page import BackupPage
from .import_page import ImportPage
from .login_dialog import DEFAULT_SERVER_URL, LoginDialog
from .report_page import ReportPage
from .status_card import StatusCard

if TYPE_CHECKING:
    from ..agent.agent_worker import AgentWorker
    from ..infrastructure.api_client.http_client import HttpApiClient
    from ..workers.status_stream_worker import StatusStreamWorker
    from ..workers.sync_worker import SyncWorker

#: 应用版本（状态栏占位展示）
APP_VERSION = "0.1.0"

#: 退出时后台线程停止等待上限（毫秒）
WORKER_STOP_TIMEOUT_MS = 5000

#: 左侧导航条目（顺序即页面堆栈顺序）
NAV_ITEMS = ("总览", "货品", "库存流水", "导入", "分析", "调度", "报告", "备份", "设置")

#: 实装页面对应的导航条目
OVERVIEW_NAV_LABEL = "总览"
ANALYSIS_NAV_LABEL = "分析"
IMPORT_NAV_LABEL = "导入"
REPORT_NAV_LABEL = "报告"
BACKUP_NAV_LABEL = "备份"

#: 安全模式只读提示（状态栏常驻，提醒用户当前会话不可写入）
SAFE_MODE_STATUS_TEXT = "安全模式（只读）：迁移未完成，导入与分析已禁用"


class MainWindow(QMainWindow):
    """工作台主窗口。"""

    def __init__(
        self,
        use_case: RunAnalysisUseCase,
        store: ResultStore,
        api_client: ApiClient | None = None,
        import_manager: CsvImportManager | None = None,
        report_manager: ReportExportManager | None = None,
        backup_manager: BackupManager | None = None,
        status_worker: StatusStreamWorker | None = None,
        agent_worker: AgentWorker | None = None,
        sync_worker: SyncWorker | None = None,
        safe_mode: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("仓库品类分析工作台")
        self.resize(1080, 680)
        self._status_worker = status_worker
        self._agent_worker = agent_worker
        self._sync_worker = sync_worker
        #: 安全模式只读标志（True：导入/分析入口禁用，无业务写入）
        self.safe_mode = safe_mode

        self._nav = QListWidget()
        for label in NAV_ITEMS:
            self._nav.addItem(QListWidgetItem(label))
        self._nav.setFixedWidth(160)

        self._pages = QStackedWidget()

        # ---- 总览页：在线状态卡片 ----
        self.status_card = StatusCard()
        overview_page = QWidget()
        overview_layout = QVBoxLayout(overview_page)
        overview_layout.addWidget(self.status_card)
        overview_layout.addStretch(1)

        self.analysis_page = AnalysisPage(use_case, store)
        self.import_page: ImportPage | None = None
        if import_manager is not None:
            self.import_page = ImportPage(import_manager)
        self.report_page: ReportPage | None = None
        if report_manager is not None:
            self.report_page = ReportPage(store, report_manager)
        self.backup_page: BackupPage | None = None
        if backup_manager is not None:
            self.backup_page = BackupPage(backup_manager)
        for label in NAV_ITEMS:
            if label == OVERVIEW_NAV_LABEL:
                self._pages.addWidget(overview_page)
            elif label == ANALYSIS_NAV_LABEL:
                self._pages.addWidget(self.analysis_page)
            elif label == IMPORT_NAV_LABEL and self.import_page is not None:
                self._pages.addWidget(self.import_page)
            elif label == REPORT_NAV_LABEL and self.report_page is not None:
                self._pages.addWidget(self.report_page)
            elif label == BACKUP_NAV_LABEL and self.backup_page is not None:
                self._pages.addWidget(self.backup_page)
            else:
                self._pages.addWidget(QWidget())  # 占位页（M1+ 逐页实装）

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(self._nav)
        layout.addWidget(self._pages, 1)
        self.setCentralWidget(central)

        self._nav.currentRowChanged.connect(self._pages.setCurrentIndex)
        self.setStatusBar(self._build_status_bar(api_client))
        self._build_help_menu()
        self._apply_safe_mode()
        self._nav.setCurrentRow(NAV_ITEMS.index(ANALYSIS_NAV_LABEL))

        # ---- 连接状态流后台线程信号到 StatusCard ----
        if status_worker is not None:
            status_worker.snapshot_received.connect(self.status_card.update_snapshot)
            status_worker.channel_changed.connect(self.status_card.update_channel)

        # ---- 同步线程待同步数到 StatusCard（M3 Task 5 总览页接线） ----
        if sync_worker is not None:
            sync_worker.sync_progress.connect(
                lambda applied, failed, pending: self.status_card.update_sync_pending(pending)
            )

    def _build_status_bar(self, api_client: ApiClient | None) -> QStatusBar:
        """顶部状态栏占位：网络/授权/待同步/版本。"""
        online = api_client.online if api_client is not None else False
        bar = QStatusBar()
        bar.addWidget(QLabel(f"网络：{'在线' if online else '离线'}"))
        bar.addWidget(QLabel("授权：未激活"))
        bar.addWidget(QLabel("待同步：0"))
        if self.safe_mode:
            # 安全模式只读提示：常驻状态栏（不永久占用右侧版本位）
            bar.addWidget(QLabel(SAFE_MODE_STATUS_TEXT))
        bar.addPermanentWidget(QLabel(f"版本：v{APP_VERSION}"))
        return bar

    # ------------------------------------------------------------------
    # 安全模式只读（M3 Task 5）：导入/分析入口禁用
    # ------------------------------------------------------------------

    def _apply_safe_mode(self) -> None:
        """``safe_mode=True`` 时禁用导入与分析入口（备份页保留可自恢复）。"""
        if not self.safe_mode:
            return
        self.analysis_page.run_button.setEnabled(False)
        self.analysis_page.run_button.setToolTip("安全模式：本地库迁移未完成，分析已禁用")
        if self.import_page is not None:
            self.import_page.set_read_only(True)

    def check_login_on_startup(self, api_client: HttpApiClient) -> bool:
        """启动登录检查：有效令牌直接放行，否则弹出登录对话框。

        :param api_client: 控制平面 HTTP 客户端（提供 login / get_account_me）。
        :return: True 表示已登录（令牌有效或对话框登录成功）；
            False 表示用户取消登录，进入离线模式（允许本地操作）。
        """
        # 已有有效令牌（get_account_me 成功）→ 直接放行
        if api_client.get_account_me() is not None:
            return True
        # 弹出登录对话框（令牌失效或首次启动）
        dialog = LoginDialog(
            api_client,
            default_server_url=DEFAULT_SERVER_URL,
            parent=self,
        )
        # 用户取消 → 离线模式（不阻断本地操作）
        return dialog.exec() == QDialog.DialogCode.Accepted

    def auto_register_device(self, api_client: HttpApiClient) -> str | None:
        """登录成功后自动注册设备：生成指纹 → 调用 register_device → 刷新状态。

        :param api_client: 控制平面 HTTP 客户端（提供 register_device）。
        :return: 设备标识（device_id）；注册失败返回 None。
        """
        from ..infrastructure.api_client.device_fingerprint import (
            generate_device_fingerprint,
            get_device_name,
        )

        fingerprint = generate_device_fingerprint()
        result = api_client.register_device(
            name=get_device_name(),
            fingerprint=fingerprint,
        )
        if result is not None:
            self.refresh_status(api_client)
            return str(result.get("device_id", ""))
        return None

    def refresh_status(self, api_client: HttpApiClient) -> None:
        """刷新状态：拉取账号信息与设备列表，更新 StatusCard。

        :param api_client: 控制平面 HTTP 客户端（提供 get_account_me / list_devices）。
        """
        account_data = api_client.get_account_me()
        if account_data is not None:
            self.status_card.update_account_context(account_data)

        devices = api_client.list_devices()
        if devices is not None:
            self.status_card.update_snapshot({"devices": devices})

    # ------------------------------------------------------------------
    # 帮助菜单：关于 / 回滚指引（M3 Task 5，文档级回滚说明）
    # ------------------------------------------------------------------

    def _build_help_menu(self) -> None:
        """菜单栏"帮助"：关于（版本信息）与回滚指引（文档级说明）。"""
        menu = self.menuBar().addMenu("帮助")
        about_action = QAction("关于", menu)
        about_action.triggered.connect(self._show_about)
        rollback_action = QAction("回滚指引", menu)
        rollback_action.triggered.connect(self._show_rollback_guide)
        menu.addAction(about_action)
        menu.addAction(rollback_action)

    def _show_about(self) -> None:
        """关于对话框：当前应用/引擎版本与本地库说明。"""
        QMessageBox.information(
            self,
            "关于",
            f"仓库品类分析工作台 v{APP_VERSION}\n\n"
            "本地数据目录：%LOCALAPPDATA%\\WarehouseWorkbench（可经 "
            "WORKBENCH_DATA_DIR 重定向）。\n"
            "每日自动备份与升级前备份保存在同级的 backups 目录。",
        )

    def _show_rollback_guide(self) -> None:
        """回滚指引对话框：文档级版本回滚步骤（不做真实降级安装）。"""
        QMessageBox.information(
            self,
            "回滚指引",
            f"当前版本：v{APP_VERSION}\n\n"
            "如需回滚到旧版本：\n"
            "1. 退出应用（托盘图标 → 退出）；\n"
            "2. 卸载当前版本并安装旧版本安装包（旧版本可从发布页获取）；\n"
            "3. 启动旧版本前，本地库会按其内置迁移自动处理；若提示迁移失败，"
            "可在安全模式中从 backups 目录的升级前备份恢复后重启。\n\n"
            "注意：本地库 schema 可能已随新版本升级，回滚后首次启动若"
            " schema 不兼容，请使用升级前备份（backups 目录）恢复数据。",
        )

    # ------------------------------------------------------------------
    # 托盘驻留与退出（M3 Task 3）
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        """窗口关闭改为隐藏驻留托盘（仅托盘"退出"真正退出应用）。"""
        event.ignore()
        self.hide()

    def quit_application(self) -> None:
        """托盘"退出"入口：先停 Agent、SSE 与同步后台线程，再退出应用。"""
        self._stop_worker(self._agent_worker)
        self._stop_worker(self._status_worker)
        self._stop_worker(self._sync_worker)
        qApp = QApplication.instance()
        if qApp is not None:
            qApp.quit()

    def _stop_worker(self, worker: AgentWorker | StatusStreamWorker | SyncWorker | None) -> None:
        """停止后台线程：stop 标志 + 有界等待（卡死时定时器兜底强退）。"""
        if worker is None or not worker.isRunning():
            return
        worker.stop()
        if not worker.wait(WORKER_STOP_TIMEOUT_MS):
            QTimer.singleShot(0, self._force_quit)

    @staticmethod
    def _force_quit() -> None:
        """线程停止超时时的兜底强退。"""
        qApp = QApplication.instance()
        if qApp is not None:
            qApp.quit()
