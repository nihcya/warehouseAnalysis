"""主窗口（§7.2）：左侧导航占位 + 顶部状态栏占位 + 页面容器。

- 导航条目：总览/货品/库存流水/导入/分析/调度/报告/备份/设置
  （M0 仅"分析"实装；M1 增加"导入"（CSV 导入向导入口页）与
  "报告"（报告导出页）/"备份"（备份恢复页），替换 M0 占位）；
- 状态栏：网络/授权/待同步/版本 占位（网络状态来自注入的 ApiClient 端口）；
- 依赖全部通过构造函数注入（组合根 ``app.main`` 组装），本模块不 import infrastructure。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
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
from .report_page import ReportPage

#: 应用版本（状态栏占位展示）
APP_VERSION = "0.1.0"

#: 左侧导航条目（顺序即页面堆栈顺序）
NAV_ITEMS = ("总览", "货品", "库存流水", "导入", "分析", "调度", "报告", "备份", "设置")

#: 实装页面对应的导航条目
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
            if label == ANALYSIS_NAV_LABEL:
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

    def _build_status_bar(self, api_client: ApiClient | None) -> QStatusBar:
        """顶部状态栏占位：网络/授权/待同步/版本。"""
        online = api_client.online if api_client is not None else False
        bar = QStatusBar()
        bar.addWidget(QLabel(f"网络：{'在线' if online else '离线'}"))
        bar.addWidget(QLabel("授权：未激活"))
        bar.addWidget(QLabel("待同步：0"))
        bar.addPermanentWidget(QLabel(f"版本：v{APP_VERSION}"))
        return bar
