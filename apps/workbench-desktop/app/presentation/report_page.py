"""报告页（M1 Task 8.3）：历史运行列表 + HTML/CSV 导出 + 打开所在目录。

- 历史 run 列表读 ``analysis_run``（经 domain 的 ResultStore 端口，
  页面不感知存储实现）；
- 对选中 run 导出 HTML / CSV（重复导出幂等更新，不报错）；
- “打开所在目录”经 ``QDesktopServices.openUrl`` 打开报告产物目录；
- 导出结果回显到页面状态标签（不弹模态框，便于无头测试与静默操作）。
"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..application.report_export import ReportExportManager
from ..domain.result_store import ResultStore

#: 历史 run 表格列（中文表头）
RUN_COLUMNS = ("运行编号", "状态", "分析期间", "引擎版本", "公式版本", "创建时间")


def _period_text(run: dict) -> str:
    """期间列文本：start_date ~ end_date（任一缺失显示占位）。"""
    start = run.get("start_date") or "—"
    end = run.get("end_date") or "—"
    return f"{start} ~ {end}"


class ReportPage(QWidget):
    """报告导出页：选历史 run → 导出 HTML/CSV → 打开产物目录。"""

    def __init__(
        self,
        store: ResultStore,
        export_manager: ReportExportManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._manager = export_manager

        title = QLabel("报告导出")
        description = QLabel(
            "从历史分析运行导出 HTML 或 CSV 报告；同一运行重复导出会重新生成"
            "并更新产物记录（幂等）。HTML 含指标与警告明细，CSV 为纯指标清单。"
        )
        description.setWordWrap(True)

        self.refresh_button = QPushButton("刷新列表")
        self.export_html_button = QPushButton("导出 HTML")
        self.export_csv_button = QPushButton("导出 CSV")
        self.open_dir_button = QPushButton("打开所在目录")

        self.run_table = QTableWidget(0, len(RUN_COLUMNS))
        self.run_table.setHorizontalHeaderLabels(list(RUN_COLUMNS))
        self.run_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.run_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.run_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.run_table.horizontalHeader().setStretchLastSection(True)

        self.status_label = QLabel("选择一次运行后导出报告")
        self.status_label.setWordWrap(True)

        actions = QHBoxLayout()
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.export_html_button)
        actions.addWidget(self.export_csv_button)
        actions.addWidget(self.open_dir_button)
        actions.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addLayout(actions)
        layout.addWidget(self.run_table, 1)
        layout.addWidget(self.status_label)

        self.refresh_button.clicked.connect(self.refresh_runs)
        self.export_html_button.clicked.connect(self._export_html)
        self.export_csv_button.clicked.connect(self._export_csv)
        self.open_dir_button.clicked.connect(self._open_reports_dir)

        self.refresh_runs()

    # ------------------------------------------------------------------
    # 列表
    # ------------------------------------------------------------------

    def refresh_runs(self) -> None:
        """重载历史 run 列表（analysis_run，新 → 旧）。"""
        runs = self._store.list_runs()
        self.run_table.setRowCount(len(runs))
        for row, run in enumerate(runs):
            cells = (
                str(run.get("run_id") or ""),
                str(run.get("status") or ""),
                _period_text(run),
                str(run.get("engine_version") or ""),
                str(run.get("formula_version") or ""),
                str(run.get("created_at") or ""),
            )
            for column, text in enumerate(cells):
                self.run_table.setItem(row, column, QTableWidgetItem(text))

    def _selected_run_id(self) -> str | None:
        """当前选中行的 run_id；未选中返回 None 并提示。"""
        row = self.run_table.currentRow()
        if row < 0:
            self.status_label.setText("请先在列表中选择一次分析运行")
            return None
        item = self.run_table.item(row, 0)
        return item.text() if item is not None else None

    # ------------------------------------------------------------------
    # 导出与打开目录
    # ------------------------------------------------------------------

    def _export_html(self) -> None:
        """导出选中 run 的 HTML 报告（重复导出幂等更新）。"""
        run_id = self._selected_run_id()
        if run_id is None:
            return
        status = self._manager.export(run_id, "HTML")
        self.status_label.setText(status.message)

    def _export_csv(self) -> None:
        """导出选中 run 的 CSV 报告（重复导出幂等更新）。"""
        run_id = self._selected_run_id()
        if run_id is None:
            return
        status = self._manager.export(run_id, "CSV")
        self.status_label.setText(status.message)

    def _open_reports_dir(self) -> None:
        """打开报告产物所在目录（资源管理器）。"""
        reports_dir = self._manager.reports_dir
        reports_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(reports_dir)))
        self.status_label.setText(f"报告目录：{reports_dir}")
