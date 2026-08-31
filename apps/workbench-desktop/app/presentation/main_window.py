"""主窗口（§7.2）：左侧导航占位 + 顶部状态栏占位 + 页面容器。

- 导航条目：总览/货品/库存流水/导入/分析/调度/报告/备份/设置
  （M0 仅"分析"实装；M1 增加"导入"（CSV 导入向导入口页）与
  "报告"（报告导出页）/"备份"（备份恢复页），替换 M0 占位）；
- 状态栏：网络/授权/待同步/版本 占位（网络状态来自注入的 ApiClient 端口）；
- 依赖全部通过构造函数注入（组合根 ``app.main`` 组装），本模块不 import infrastructure。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
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
    from ..infrastructure.api_client.http_client import HttpApiClient
    from ..workers.status_stream_worker import StatusStreamWorker

#: 应用版本（状态栏占位展示）
APP_VERSION = "0.1.0"

#: 左侧导航条目（顺序即页面堆栈顺序）
NAV_ITEMS = ("总览", "货品", "库存流水", "导入", "分析", "调度", "报告", "备份", "设置")

#: 实装页面对应的导航条目
OVERVIEW_NAV_LABEL = "总览"
ANALYSIS_NAV_LABEL = "分析"
IMPORT_NAV_LABEL = "导入"
REPORT_NAV_LABEL = "报告"
BACKUP_NAV_LABEL = "备份"


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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("仓库品类分析工作台")
        self.resize(1080, 680)

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
        self._nav.setCurrentRow(NAV_ITEMS.index(ANALYSIS_NAV_LABEL))

        # ---- 连接状态流后台线程信号到 StatusCard ----
        if status_worker is not None:
            status_worker.snapshot_received.connect(self.status_card.update_snapshot)
            status_worker.channel_changed.connect(self.status_card.update_channel)

    def _build_status_bar(self, api_client: ApiClient | None) -> QStatusBar:
        """顶部状态栏占位：网络/授权/待同步/版本。"""
        online = api_client.online if api_client is not None else False
        bar = QStatusBar()
        bar.addWidget(QLabel(f"网络：{'在线' if online else '离线'}"))
        bar.addWidget(QLabel("授权：未激活"))
        bar.addWidget(QLabel("待同步：0"))
        bar.addPermanentWidget(QLabel(f"版本：v{APP_VERSION}"))
        return bar

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

    def auto_register_device(self, api_client: HttpApiClient) -> None:
        """登录成功后自动注册设备：生成指纹 → 调用 register_device → 刷新状态。

        :param api_client: 控制平面 HTTP 客户端（提供 register_device）。
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
