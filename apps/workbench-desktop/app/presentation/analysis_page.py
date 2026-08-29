"""分析结果页（§7.5 + M1 Task 7.3）：页内页签切换“运行分析 / 历史运行”。

- 进度条由引擎 progress 回调驱动（0.0~1.0 → 0~100）；
- 结果表格固定列：run_id、engine_version、formula_version、metrics、warnings；
- 校验失败时展示错误列表且不调用 analyze、不落库；
  无数据（空库或仅导入主数据）时展示提示，不抛异常；
- 历史运行页列出 analysis_run（run_id、期间、状态、引擎/公式版本、时间），
  双击记录或选中后点“查看结果”按 run_id 加载到本页结果表格
  （复用 M0 结果展示逻辑；不改 main_window 导航）；
- 页面只依赖 application 用例与 domain 端口，不含任何引擎/存储实现分支。
"""

from __future__ import annotations

from typing import Any

from contracts import AnalysisResult
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..application.analysis_usecase import AnalysisOutcome, RunAnalysisUseCase
from ..domain.result_store import ResultStore

#: 结果表格列（§7.5 冻结）
RESULT_COLUMNS = ("run_id", "engine_version", "formula_version", "metrics", "warnings")

#: 历史运行表格列（M1 Task 7.3）
HISTORY_COLUMNS = ("运行 ID", "期间", "状态", "引擎版本", "公式版本", "运行时间")

#: 页内页签下标
RUN_TAB_INDEX = 0
HISTORY_TAB_INDEX = 1

#: analysis_run.status → 中文展示（未知状态回退原值）
_RUN_STATUS_LABELS = {
    "CREATED": "已创建",
    "QUEUED": "排队中",
    "RUNNING": "运行中",
    "SUCCEEDED": "成功",
    "FAILED": "失败",
    "CANCELLED": "已取消",
}

#: 无数据提示文案（空库或仅导入主数据）
NO_DATA_MESSAGE = "本地库暂无分析数据：请先通过“导入”页录入主数据与库存事件"


def _format_metrics(result: AnalysisResult) -> str:
    """指标列文本：``名称=值单位`` 以分号连接。"""
    return "; ".join(f"{metric.name}={metric.value}{metric.unit}" for metric in result.metrics)


def _format_warnings(result: AnalysisResult) -> str:
    """警告列文本：``code: message`` 以分号连接。"""
    return "; ".join(f"{warning.code}: {warning.message}" for warning in result.warnings)


class AnalysisPage(QWidget):
    """分析结果页：一次运行展示一行结果；含“历史运行”页与按 run_id 重查。"""

    def __init__(
        self,
        use_case: RunAnalysisUseCase,
        store: ResultStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._use_case = use_case
        self._store = store

        # ---- “运行分析”页（M0 结构原样，仅移入页签）----
        self.run_button = QPushButton("运行分析")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)

        self.run_id_input = QLineEdit()
        self.run_id_input.setPlaceholderText("按 run_id 查看历史结果")
        self.view_button = QPushButton("查看")

        self.result_table = QTableWidget(0, len(RESULT_COLUMNS))
        self.result_table.setHorizontalHeaderLabels(list(RESULT_COLUMNS))
        self.result_table.horizontalHeader().setStretchLastSection(True)

        self.issue_list = QListWidget()
        self.issue_list.setVisible(False)

        actions = QHBoxLayout()
        actions.addWidget(self.run_button)
        actions.addWidget(self.progress_bar, 1)

        lookup = QHBoxLayout()
        lookup.addWidget(self.run_id_input, 1)
        lookup.addWidget(self.view_button)

        run_layout = QVBoxLayout()
        run_layout.addLayout(actions)
        run_layout.addLayout(lookup)
        run_layout.addWidget(self.result_table, 1)
        run_layout.addWidget(self.issue_list)
        run_panel = QWidget()
        run_panel.setLayout(run_layout)

        # ---- “历史运行”页（M1 Task 7.3）----
        self.history_table = QTableWidget(0, len(HISTORY_COLUMNS))
        self.history_table.setHorizontalHeaderLabels(list(HISTORY_COLUMNS))
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        self.load_run_button = QPushButton("查看结果")

        history_actions = QHBoxLayout()
        history_actions.addWidget(self.load_run_button)
        history_actions.addStretch(1)

        history_layout = QVBoxLayout()
        history_layout.addLayout(history_actions)
        history_layout.addWidget(self.history_table, 1)
        history_panel = QWidget()
        history_panel.setLayout(history_layout)

        self.tabs = QTabWidget()
        self.tabs.addTab(run_panel, "运行分析")
        self.tabs.addTab(history_panel, "历史运行")

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)

        self.run_button.clicked.connect(self._run_analysis)
        self.view_button.clicked.connect(self._view_run)
        self.load_run_button.clicked.connect(self._load_selected_run)
        self.history_table.itemDoubleClicked.connect(self._load_run_from_item)
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_progress(self, fraction: float) -> None:
        """progress 回调（引擎在计算线程内调用；M0 同步执行于 UI 线程）。"""
        self.progress_bar.setValue(round(fraction * 100))

    def _run_analysis(self) -> None:
        """触发一次分析用例：无数据展示提示，校验失败展示错误列表，成功展示结果行。"""
        self.progress_bar.setValue(0)
        self.issue_list.clear()
        outcome = self._use_case.run(progress=self._on_progress)
        if outcome.no_data:
            self._show_message(NO_DATA_MESSAGE)
            return
        if outcome.result is None:
            self._show_issues(outcome)
            return
        self._show_result(outcome.result)
        self.refresh_history()

    def refresh_history(self) -> None:
        """从 ResultStore 读取最近运行列表并填充历史表格（新 → 旧）。"""
        runs = self._store.list_runs()
        self.history_table.setRowCount(len(runs))
        for row, run in enumerate(runs):
            cells = self._history_cells(run)
            for column, text in enumerate(cells):
                self.history_table.setItem(row, column, QTableWidgetItem(text))

    @staticmethod
    def _history_cells(run: dict[str, Any]) -> tuple[str, ...]:
        """历史运行摘要字典 → 表格行文本（空值以占位符/空串展示）。"""
        status = run.get("status") or ""
        return (
            run.get("run_id") or "",
            f"{run.get('start_date') or '?'} ~ {run.get('end_date') or '?'}",
            _RUN_STATUS_LABELS.get(status, status),
            run.get("engine_version") or "",
            run.get("formula_version") or "",
            run.get("finished_at") or run.get("created_at") or "",
        )

    def _on_tab_changed(self, index: int) -> None:
        """切到“历史运行”页时刷新列表（运行记录随落库增长）。"""
        if index == HISTORY_TAB_INDEX:
            self.refresh_history()

    def _load_run_from_item(self, item: QTableWidgetItem) -> None:
        """双击历史记录：按该行 run_id 加载结果到结果表格。"""
        self._load_run_id(item.text())

    def _load_selected_run(self) -> None:
        """“查看结果”按钮：按选中行 run_id 加载结果到结果表格。"""
        row = self.history_table.currentRow()
        item = self.history_table.item(row, 0) if row >= 0 else None
        if item is None:
            return
        self._load_run_id(item.text())

    def _load_run_id(self, run_id: str) -> None:
        """按 run_id 取回结果，展示在“运行分析”页结果表格（复用 M0 展示逻辑）。"""
        result = self._store.get(run_id)
        if result is None:
            self.result_table.setRowCount(0)
            self.issue_list.clear()
            self.issue_list.setVisible(True)
            self.issue_list.addItem(f"未找到运行结果：{run_id}")
            return
        self._show_result(result)
        # 结果展示在“运行分析”页的结果表格，加载后切回该页
        self.tabs.setCurrentIndex(RUN_TAB_INDEX)

    def _show_message(self, message: str) -> None:
        """非错误类提示（无数据等）：清空结果表格，在提示列表展示。"""
        self.result_table.setRowCount(0)
        self.issue_list.clear()
        self.issue_list.setVisible(True)
        self.issue_list.addItem(message)

    def _show_issues(self, outcome: AnalysisOutcome) -> None:
        """校验失败：清空结果表格，展示错误列表（不调用 analyze、不落库）。"""
        self.result_table.setRowCount(0)
        self.issue_list.setVisible(True)
        for issue in outcome.issues:
            self.issue_list.addItem(f"[{issue.code.value}] {issue.field}: {issue.reason}")

    def _show_result(self, result: AnalysisResult) -> None:
        """成功：结果表格写入一行，并把 run_id 回填到查询框。"""
        self.issue_list.clear()
        self.issue_list.setVisible(False)
        cells = (
            result.run_id,
            result.engine_version,
            result.formula_version,
            _format_metrics(result),
            _format_warnings(result),
        )
        self.result_table.setRowCount(1)
        for column, text in enumerate(cells):
            self.result_table.setItem(0, column, QTableWidgetItem(text))
        self.run_id_input.setText(result.run_id)

    def _view_run(self) -> None:
        """按 run_id 从 ResultStore 取回历史结果并重新展示。"""
        run_id = self.run_id_input.text().strip()
        if not run_id:
            return
        result = self._store.get(run_id)
        if result is None:
            self.result_table.setRowCount(0)
            self.issue_list.setVisible(True)
            self.issue_list.addItem(f"未找到 run_id：{run_id}")
            return
        self._show_result(result)
