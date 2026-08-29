"""分析结果页（§7.5）：运行分析 + 进度条 + 结果表格 + 校验错误列表 + 按 run_id 重查。

- 进度条由引擎 progress 回调驱动（0.0~1.0 → 0~100）；
- 结果表格固定列：run_id、engine_version、formula_version、metrics、warnings；
- 校验失败时展示错误列表且不调用 analyze、不落库；
- 页面只依赖 application 用例与 domain 端口，不含任何引擎/存储实现分支。
"""

from __future__ import annotations

from contracts import AnalysisResult
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..application.analysis_usecase import AnalysisOutcome, RunAnalysisUseCase
from ..domain.result_store import ResultStore

#: 结果表格列（§7.5 冻结）
RESULT_COLUMNS = ("run_id", "engine_version", "formula_version", "metrics", "warnings")


def _format_metrics(result: AnalysisResult) -> str:
    """指标列文本：``名称=值单位`` 以分号连接。"""
    return "; ".join(f"{metric.name}={metric.value}{metric.unit}" for metric in result.metrics)


def _format_warnings(result: AnalysisResult) -> str:
    """警告列文本：``code: message`` 以分号连接。"""
    return "; ".join(f"{warning.code}: {warning.message}" for warning in result.warnings)


class AnalysisPage(QWidget):
    """分析结果页：一次运行展示一行结果；校验失败展示错误列表。"""

    def __init__(
        self,
        use_case: RunAnalysisUseCase,
        store: ResultStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._use_case = use_case
        self._store = store

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

        layout = QVBoxLayout(self)
        layout.addLayout(actions)
        layout.addLayout(lookup)
        layout.addWidget(self.result_table, 1)
        layout.addWidget(self.issue_list)

        self.run_button.clicked.connect(self._run_analysis)
        self.view_button.clicked.connect(self._view_run)

    def _on_progress(self, fraction: float) -> None:
        """progress 回调（引擎在计算线程内调用；M0 同步执行于 UI 线程）。"""
        self.progress_bar.setValue(round(fraction * 100))

    def _run_analysis(self) -> None:
        """触发一次分析用例：校验失败展示错误列表，成功展示结果行。"""
        self.progress_bar.setValue(0)
        self.issue_list.clear()
        outcome = self._use_case.run(progress=self._on_progress)
        if outcome.result is None:
            self._show_issues(outcome)
            return
        self._show_result(outcome.result)

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
