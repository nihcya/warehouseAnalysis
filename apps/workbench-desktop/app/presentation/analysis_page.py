"""分析结果页（§7.5 + M1 Task 7.3 + M2 Issue #9/#11）。

- 进度条由引擎 progress 回调驱动（0.0~1.0 → 0~100）；
- **M2（Issue #9）**：结果区由「一行塞满 18 个指标的长文本」改为
  **指标明细表**（指标 / 值 / 单位 / 公式 ID / 说明），
  engine 0.3.0 的五类公式共 18 个指标逐行可读；
- **M2（Issue #11）**：新增**告警表**（级别 / 代码 / 说明 / 字段），
  按引擎返回顺序展示（校验层 → KPI → abc-aging → 补货 → 预测 → 基准），
  阻断错误与非阻断告警视觉区分；
- 校验失败时展示错误列表且不调用 analyze、不落库；
  无数据（空库或仅导入主数据）时展示提示，不抛异常；
- 历史运行页列出 analysis_run（run_id、期间、状态、引擎/公式版本、时间），
  双击记录或选中后点“查看结果”按 run_id 加载到本页结果区；
- 页面只依赖 application 用例与 domain 端口，不含任何引擎/存储实现分支。

术语映射说明：指标名 / 告警码的中文文案**只用于展示**，未知取值一律回退英文原名，
避免 UI 因新增指标或告警码而显示空值（引擎演进不阻断 UI）。
"""

from __future__ import annotations

from typing import Any

from contracts import AnalysisResult
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
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

#: 指标明细表列（M2 Issue #9）
METRIC_COLUMNS = ("指标", "值", "单位", "公式 ID", "说明")

#: 告警表列（M2 Issue #11）
WARNING_COLUMNS = ("级别", "代码", "说明", "字段")

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

#: 空值展示占位（ResultMetric 不支持 null，以字符串 "null" 表示）
NULL_DISPLAY = "—"

#: 指标名 → 中文（engine 0.3.0 五类公式共 18 个指标）
METRIC_LABELS: dict[str, str] = {
    "KPI.OPENING_QTY": "期初库存",
    "KPI.CLOSING_QTY": "期末库存",
    "KPI.OUT_QTY": "出库量",
    "KPI.ACTIVE_RATIO": "动销率",
    "KPI.INVENTORY_VALUE": "库存价值",
    "KPI.COGS": "销售成本（COGS）",
    "KPI.TURNOVER": "周转率",
    "KPI.TURNOVER_DAYS": "周转天数",
    "KPI.COVERAGE_DAYS": "覆盖天数",
    "ABC.CLASSIFIED_SKU_COUNT": "ABC 分层 SKU 数",
    "AGING.AVG_AGE_DAYS": "平均库龄（天）",
    "STALE.AMOUNT": "呆滞金额",
    "REPL.SAFETY_STOCK_TOTAL": "安全库存合计",
    "REPL.REORDER_POINT_TOTAL": "补货点合计",
    "REPL.SUGGESTED_QTY_TOTAL": "建议补货量合计",
    "FCST.NEXT_WEEK_DEMAND": "下周需求预测",
    "FCST.MAPE": "预测误差 MAPE",
    "BM.DEVIATION_RATIO": "行业基准平均偏差率",
}

#: 降级原因 reason → 中文（引擎侧仅此 6 个取值）
REASON_LABELS: dict[str, str] = {
    "empty_dataset": "动销率分母为空（无库存或无事件）",
    "avg_inventory_value_nonpositive": "平均库存价值非正",
    "turnover_nonpositive": "周转率非正",
    "nonpositive_closing": "期末库存非正",
    "zero_period_days": "分析期间天数为 0",
    "no_outflow": "期间内无出库",
}

#: Warning 码 → 中文（引擎 0.3.0 会产生的全部告警码）
WARNING_LABELS: dict[str, str] = {
    "ANALYSIS_PLACEHOLDER": "该能力尚为占位实现",
    "BENCHMARK_UNAVAILABLE": "无匹配的行业基准数据",
    "DATE_MISSING": "缺少日期，无法参与该口径计算",
    "INSUFFICIENT_SAMPLES": "样本不足，预测结果已降级",
    "NEGATIVE_BALANCE": "存在负库存，相关口径已按 0 处理",
    "NO_OUTFLOW": "期间内无出库，相关口径无法计算",
    "PARAM_MISSING": "缺少计算参数，未输出该建议",
    "PERIOD_MISMATCH": "存在超出分析期间的数据",
    "UNIT_COST_MISSING": "缺少单位成本，已按快照价值回退",
}

#: Warning 严重级别 → 中文
SEVERITY_LABELS: dict[str, str] = {
    "INFO": "提示",
    "WARN": "警告",
    "ERROR": "错误",
}


def _metric_label(name: str) -> str:
    """指标名 → 中文；未知指标回退原名（引擎新增指标不阻断 UI）。"""
    return METRIC_LABELS.get(name, name)


def _reason_label(reason: str | None) -> str:
    """降级原因 → 中文；无原因或未知时返回空串。"""
    if not reason:
        return ""
    return REASON_LABELS.get(reason, reason)


def _warning_label(code: str) -> str:
    """告警码 → 中文；未知告警码回退原码。"""
    return WARNING_LABELS.get(code, code)


def _severity_label(severity: str) -> str:
    """严重级别 → 中文；未知级别回退原值。"""
    return SEVERITY_LABELS.get(severity, severity)


def _format_value(value: object) -> str:
    """指标值 → 展示文本（``"null"`` 以占位符呈现）。"""
    if value is None:
        return NULL_DISPLAY
    if isinstance(value, str) and value.strip().lower() == "null":
        return NULL_DISPLAY
    return str(value)


class AnalysisPage(QWidget):
    """分析结果页：指标明细表 + 告警表 + 历史运行页与按 run_id 重查。"""

    def __init__(
        self,
        use_case: RunAnalysisUseCase,
        store: ResultStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._use_case = use_case
        self._store = store

        # ---- “运行分析”页 ----
        self.run_button = QPushButton("运行分析")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)

        self.run_id_input = QLineEdit()
        self.run_id_input.setPlaceholderText("按 run_id 查看历史结果")
        self.view_button = QPushButton("查看")

        # 元信息（运行标识 / 引擎版本 / 公式版本 / 基准版本）
        self.meta_run_id = QLabel("—")
        self.meta_engine = QLabel("—")
        self.meta_formula = QLabel("—")
        self.meta_benchmark = QLabel("—")
        meta_form = QFormLayout()
        meta_form.addRow("运行 ID：", self.meta_run_id)
        meta_form.addRow("引擎版本：", self.meta_engine)
        meta_form.addRow("公式版本：", self.meta_formula)
        meta_form.addRow("基准版本：", self.meta_benchmark)
        meta_box = QGroupBox("运行信息")
        meta_box.setLayout(meta_form)

        # 指标明细表（Issue #9：18 个指标逐行展示，不再挤在一个单元格）
        self.metrics_table = QTableWidget(0, len(METRIC_COLUMNS))
        self.metrics_table.setHorizontalHeaderLabels(list(METRIC_COLUMNS))
        self.metrics_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.metrics_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.metrics_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.metrics_table.horizontalHeader().setStretchLastSection(True)
        metrics_box = QGroupBox("指标明细")
        metrics_layout = QVBoxLayout()
        metrics_layout.addWidget(self.metrics_table)
        metrics_box.setLayout(metrics_layout)

        # 告警表（Issue #11：结构化展示 code / fields / reason）
        self.warnings_table = QTableWidget(0, len(WARNING_COLUMNS))
        self.warnings_table.setHorizontalHeaderLabels(list(WARNING_COLUMNS))
        self.warnings_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.warnings_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.warnings_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.warnings_table.horizontalHeader().setStretchLastSection(True)
        warnings_box = QGroupBox("告警")
        warnings_layout = QVBoxLayout()
        warnings_layout.addWidget(self.warnings_table)
        warnings_box.setLayout(warnings_layout)

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
        run_layout.addWidget(meta_box)
        run_layout.addWidget(metrics_box, 2)
        run_layout.addWidget(warnings_box, 1)
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
        """触发一次分析用例：无数据展示提示，校验失败展示错误列表，成功展示结果。"""
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
        """双击历史记录：按该行 run_id 加载结果到结果区。"""
        self._load_run_id(item.text())

    def _load_selected_run(self) -> None:
        """“查看结果”按钮：按选中行 run_id 加载结果到结果区。"""
        row = self.history_table.currentRow()
        item = self.history_table.item(row, 0) if row >= 0 else None
        if item is None:
            return
        self._load_run_id(item.text())

    def _load_run_id(self, run_id: str) -> None:
        """按 run_id 取回结果，展示在“运行分析”页结果区。"""
        result = self._store.get(run_id)
        if result is None:
            self._clear_result_tables()
            self.issue_list.clear()
            self.issue_list.setVisible(True)
            self.issue_list.addItem(f"未找到运行结果：{run_id}")
            return
        self._show_result(result)
        # 结果展示在“运行分析”页，加载后切回该页
        self.tabs.setCurrentIndex(RUN_TAB_INDEX)

    def _show_message(self, message: str) -> None:
        """非错误类提示（无数据等）：清空结果区，在提示列表展示。"""
        self._clear_result_tables()
        self._show_meta(run_id="", engine="", formula="", benchmark="")
        self.issue_list.clear()
        self.issue_list.setVisible(True)
        self.issue_list.addItem(message)

    def _show_issues(self, outcome: AnalysisOutcome) -> None:
        """校验失败：清空结果区，展示错误列表（不调用 analyze、不落库）。"""
        self._clear_result_tables()
        self._show_meta(run_id=outcome.run_id, engine="", formula="", benchmark="")
        self.issue_list.setVisible(True)
        for issue in outcome.issues:
            self.issue_list.addItem(f"[{issue.code.value}] {issue.field}: {issue.reason}")

    def _show_result(self, result: AnalysisResult) -> None:
        """成功：填充元信息、指标明细表与告警表，并回填 run_id。"""
        self.issue_list.clear()
        self.issue_list.setVisible(False)
        self._show_meta(
            run_id=result.run_id,
            engine=result.engine_version,
            formula=result.formula_version,
            benchmark=self._use_case.benchmark_version or "未配置",
        )
        self._fill_metrics(result)
        self._fill_warnings(result)
        self.run_id_input.setText(result.run_id)

    def _show_meta(self, *, run_id: str, engine: str, formula: str, benchmark: str) -> None:
        """写入运行信息区（空值以占位符展示）。"""
        self.meta_run_id.setText(run_id or NULL_DISPLAY)
        self.meta_engine.setText(engine or NULL_DISPLAY)
        self.meta_formula.setText(formula or NULL_DISPLAY)
        self.meta_benchmark.setText(benchmark or NULL_DISPLAY)

    def _fill_metrics(self, result: AnalysisResult) -> None:
        """填充指标明细表：每个指标一行（Issue #9）。"""
        self.metrics_table.setRowCount(len(result.metrics))
        for row, metric in enumerate(result.metrics):
            cells = (
                _metric_label(metric.name),
                _format_value(metric.value),
                metric.unit,
                metric.formula_id,
                _reason_label(metric.reason),
            )
            for column, text in enumerate(cells):
                self.metrics_table.setItem(row, column, QTableWidgetItem(text))

    def _fill_warnings(self, result: AnalysisResult) -> None:
        """填充告警表：按引擎返回顺序逐条展示（Issue #11）。"""
        self.warnings_table.setRowCount(len(result.warnings))
        for row, warning in enumerate(result.warnings):
            severity = getattr(warning.severity, "value", str(warning.severity))
            code = getattr(warning.code, "value", str(warning.code))
            level = _severity_label(severity)
            if warning.blocking:
                level = f"{level}（阻断）"
            cells = (
                level,
                code,
                _warning_label(code),
                ", ".join(warning.fields) if warning.fields else "",
            )
            for column, text in enumerate(cells):
                self.warnings_table.setItem(row, column, QTableWidgetItem(text))

    def _clear_result_tables(self) -> None:
        """清空指标表与告警表（无结果 / 校验失败场景）。"""
        self.metrics_table.setRowCount(0)
        self.warnings_table.setRowCount(0)

    def _view_run(self) -> None:
        """按 run_id 从 ResultStore 取回历史结果并重新展示。"""
        run_id = self.run_id_input.text().strip()
        if not run_id:
            return
        result = self._store.get(run_id)
        if result is None:
            self._clear_result_tables()
            self.issue_list.setVisible(True)
            self.issue_list.addItem(f"未找到 run_id：{run_id}")
            return
        self._show_result(result)
