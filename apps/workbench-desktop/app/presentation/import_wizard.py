"""导入向导（M1 Task 6）：五步 QWizard，错误隔离闭环的展示层。

文件选择 → 导入类型与字段映射 → 预览前 20 行 → 执行与结果 → 错误明细页。

- 文件选择：QFileDialog；显示文件名/大小/编码探测结果；
- 字段映射：读 CSV 表头，每个契约字段一个 QComboBox（默认按名称
  精确/模糊匹配预选）；缺必填映射时"下一步"禁用并给出提示；
- 预览：QTableWidget 展示前 20 行原始数据；
- 执行：同步调用导入用例（M1 数据量小）；完成页显示
  inserted / skipped / error 汇总；
- 重复导入：弹窗告知"该文件已导入过（批次 xxx）"，不继续导入；
- 错误页：QTableWidget（行号/字段/错误码/原始值/修复建议），
  数据来自 ``import_error`` 查询。

本模块只依赖 application 用例（不含引擎/存储实现分支）；
弹窗（QMessageBox）只出现在按钮回调内，逻辑方法可独立测试。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from ..application.import_manager import (
    EVENT_OPTIONAL_FIELDS,
    EVENT_REQUIRED_FIELDS,
    IMPORT_TYPE_EVENTS,
    IMPORT_TYPE_MASTER,
    MASTER_OPTIONAL_FIELDS,
    MASTER_REQUIRED_FIELDS,
    CsvImportError,
    CsvImportManager,
    CsvSnapshot,
    DuplicateImportFound,
    ImportRunSummary,
)

#: 预览行数上限（spec：预览前 20 行）
PREVIEW_ROW_LIMIT = 20

#: 错误明细页表格列
ERROR_COLUMNS = ("行号", "字段", "错误码", "原始值", "修复建议")

#: 映射下拉框第 0 项占位文案（未映射 / 不导入）
_UNMAPPED = "（不导入）"

#: 导入类型选项（向导展示名 → 导入类型常量）
_IMPORT_TYPE_CHOICES: tuple[tuple[str, str], ...] = (
    ("主数据（SKU）", IMPORT_TYPE_MASTER),
    ("库存事件", IMPORT_TYPE_EVENTS),
)

#: 契约字段的中文名（映射页标签）
_FIELD_LABELS: dict[str, str] = {
    "sku_id": "SKU 编号",
    "name": "名称",
    "category": "品类",
    "sub_category": "子品类",
    "unit": "单位",
    "unit_cost": "单位成本",
    "industry": "行业",
    "event_id": "事件编号",
    "warehouse_id": "仓库编号",
    "move_type": "事件类型",
    "quantity": "数量",
    "occurred_at": "发生日期",
    "source_ref": "来源单据号",
}


def _normalize_header(name: str) -> str:
    """表头归一化：去空白/下划线/连字符并转小写（精确匹配口径）。"""
    return re.sub(r"[\s_\-]+", "", name).lower()


#: 模糊匹配的最小字段长度（避免 unit 误配 unit_cost、name 误配 file_name）
_FUZZY_MIN_LENGTH = 6


def suggest_column(field: str, fieldnames: Sequence[str]) -> str | None:
    """契约字段默认映射的 CSV 列：归一化精确匹配优先，其次保守模糊匹配。"""
    field_key = _normalize_header(field)
    for name in fieldnames:
        if _normalize_header(name) == field_key:
            return name
    if len(field_key) >= _FUZZY_MIN_LENGTH:
        for name in fieldnames:
            if field_key in _normalize_header(name):
                return name
    return None


def _format_size(size: int) -> str:
    """文件大小的人类可读表示（B / KB / MB）。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.2f} MB"


def _field_label(field: str, required: bool) -> str:
    """映射页字段标签：中文名（契约字段，必填/可选）。"""
    chinese = _FIELD_LABELS.get(field, field)
    mark = "必填" if required else "可选"
    return f"{chinese}（{field}，{mark}）"


class ImportWizard(QWizard):
    """CSV 导入向导：五步页面顺序与共享状态（快照/执行结果）。"""

    def __init__(
        self,
        manager: CsvImportManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("数据导入向导")
        self.resize(760, 540)
        self.manager = manager
        #: 文件选择页载入的 CSV 快照（None 时不可进入后续步骤）
        self.snapshot: CsvSnapshot | None = None
        #: 最近一次执行结果（导入汇总或重复检出）
        self.import_result: ImportRunSummary | DuplicateImportFound | None = None

        self.file_page = FileSelectPage(self)
        self.mapping_page = MappingPage(self)
        self.preview_page = PreviewPage(self)
        self.execute_page = ExecutePage(self)
        self.error_page = ErrorPage(self)
        for page in (
            self.file_page,
            self.mapping_page,
            self.preview_page,
            self.execute_page,
            self.error_page,
        ):
            self.addPage(page)

        self.setButtonText(QWizard.WizardButton.NextButton, "下一步")
        self.setButtonText(QWizard.WizardButton.BackButton, "上一步")
        self.setButtonText(QWizard.WizardButton.CancelButton, "取消")
        self.setButtonText(QWizard.WizardButton.FinishButton, "完成")

    @property
    def last_batch_id(self) -> str | None:
        """最近一次成功执行的导入批次号（错误明细页数据源）。"""
        return (
            self.import_result.batch_id
            if isinstance(self.import_result, ImportRunSummary)
            else None
        )


class FileSelectPage(QWizardPage):
    """第 1 步：选择 CSV 文件，显示文件名/大小/编码探测结果。"""

    def __init__(self, wizard: ImportWizard) -> None:
        super().__init__()
        self.setTitle("选择文件")
        self.setSubTitle("选择要导入的 CSV 文件（编码自动探测：UTF-8 / GBK）")
        self._wizard = wizard

        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("尚未选择文件")
        self.browse_button = QPushButton("浏览...")
        self.info_label = QLabel("尚未选择文件")
        self.info_label.setWordWrap(True)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)

        row = QHBoxLayout()
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.browse_button)

        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.info_label)
        layout.addWidget(self.error_label)
        layout.addStretch(1)

        self.browse_button.clicked.connect(self._browse)

    def _browse(self) -> None:
        """QFileDialog 选择 CSV 文件并载入快照。"""
        selected, _filter = QFileDialog.getOpenFileName(
            self, "选择 CSV 文件", "", "CSV 文件 (*.csv);;所有文件 (*)"
        )
        if selected:
            self.set_file(Path(selected))

    def set_file(self, path: Path) -> None:
        """载入文件：探测编码并读取快照；失败展示错误并阻断下一步。"""
        self.path_edit.setText(str(path))
        try:
            snapshot = self._wizard.manager.read_csv(path)
        except CsvImportError as exc:
            self._wizard.snapshot = None
            self.info_label.setText("文件无法读取")
            self.error_label.setText(exc.message)
            self.error_label.setVisible(True)
            self.completeChanged.emit()
            return
        self._wizard.snapshot = snapshot
        self.info_label.setText(
            f"文件：{path.name}\n"
            f"大小：{_format_size(path.stat().st_size)}\n"
            f"编码：{snapshot.encoding}\n"
            f"表头列：{len(snapshot.fieldnames)} 个，数据行：{len(snapshot.rows)} 行"
        )
        self.error_label.clear()
        self.error_label.setVisible(False)
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        """已成功读取快照才允许进入下一步。"""
        return self._wizard.snapshot is not None


class MappingPage(QWizardPage):
    """第 2 步：导入类型与字段映射（缺必填映射时禁用"下一步"并提示）。"""

    def __init__(self, wizard: ImportWizard) -> None:
        super().__init__()
        self.setTitle("导入类型与字段映射")
        self.setSubTitle("选择导入类型，把每个契约字段映射到 CSV 列（必填字段不可留空）")
        self._wizard = wizard
        self._combos: dict[str, QComboBox] = {}

        self.type_combo = QComboBox()
        for label, _kind in _IMPORT_TYPE_CHOICES:
            self.type_combo.addItem(label)
        self.type_combo.currentIndexChanged.connect(lambda _index: self._rebuild_fields())

        self.fields_grid = QGridLayout()
        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.setVisible(False)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("导入类型："))
        type_row.addWidget(self.type_combo)
        type_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(type_row)
        layout.addLayout(self.fields_grid)
        layout.addWidget(self.hint_label)
        layout.addStretch(1)

    def initializePage(self) -> None:
        """进入页面时按当前 CSV 表头重建映射控件（含默认预选）。"""
        self._rebuild_fields()

    def current_import_type(self) -> str:
        """当前选择的导入类型常量。"""
        return _IMPORT_TYPE_CHOICES[self.type_combo.currentIndex()][1]

    def field_combo(self, field: str) -> QComboBox | None:
        """按契约字段取映射下拉框。"""
        return self._combos.get(field)

    def _field_spec(self) -> list[tuple[str, bool]]:
        """当前导入类型的 (契约字段, 是否必填) 列表。"""
        if self.current_import_type() == IMPORT_TYPE_MASTER:
            required, optional = MASTER_REQUIRED_FIELDS, MASTER_OPTIONAL_FIELDS
        else:
            required, optional = EVENT_REQUIRED_FIELDS, EVENT_OPTIONAL_FIELDS
        return [(field, True) for field in required] + [
            (field, False) for field in optional
        ]

    def _rebuild_fields(self) -> None:
        """按快照表头重建字段映射行；默认按名称精确/模糊匹配预选。"""
        while self.fields_grid.count():
            item = self.fields_grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._combos.clear()

        snapshot = self._wizard.snapshot
        if snapshot is None:
            return
        for row, (field, required) in enumerate(self._field_spec()):
            label = QLabel(_field_label(field, required))
            combo = QComboBox()
            combo.addItem(_UNMAPPED)  # 第 0 项：未映射/不导入
            for name in snapshot.fieldnames:
                combo.addItem(name)
            suggested = suggest_column(field, snapshot.fieldnames)
            if suggested is not None:
                combo.setCurrentText(suggested)
            combo.currentIndexChanged.connect(self._on_combo_changed)
            self.fields_grid.addWidget(label, row, 0)
            self.fields_grid.addWidget(combo, row, 1)
            self._combos[field] = combo
        self._update_hint()
        self.completeChanged.emit()

    def _on_combo_changed(self, _index: int) -> None:
        """映射变化时刷新提示与完成状态。"""
        self._update_hint()
        self.completeChanged.emit()

    def _missing_required(self) -> list[str]:
        """未映射的必填字段列表。"""
        missing: list[str] = []
        for field, required in self._field_spec():
            if not required:
                continue
            combo = self._combos.get(field)
            if combo is None or combo.currentIndex() == 0:
                missing.append(field)
        return missing

    def _update_hint(self) -> None:
        """缺必填映射时给出提示文案。"""
        missing = self._missing_required()
        if missing:
            self.hint_label.setText("缺少必填字段映射：" + "、".join(missing))
            self.hint_label.setVisible(True)
        else:
            self.hint_label.clear()
            self.hint_label.setVisible(False)

    def isComplete(self) -> bool:
        """全部必填字段已映射才允许进入下一步。"""
        return not self._missing_required()

    def collect_mapping(self) -> tuple[str, dict[str, str]]:
        """收集导入类型与字段映射（契约字段 → CSV 列名，未映射字段不包含）。"""
        mapping = {
            field: combo.currentText()
            for field, combo in self._combos.items()
            if combo.currentIndex() > 0
        }
        return self.current_import_type(), mapping


class PreviewPage(QWizardPage):
    """第 3 步：预览前 20 行原始数据。"""

    def __init__(self, wizard: ImportWizard) -> None:
        super().__init__()
        self.setTitle("预览")
        self.setSubTitle(f"前 {PREVIEW_ROW_LIMIT} 行原始数据（仅供核对，不含校验结果）")
        self._wizard = wizard

        self.preview_table = QTableWidget(0, 0)
        self.preview_table.horizontalHeader().setStretchLastSection(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.preview_table)

    def initializePage(self) -> None:
        """进入页面时按快照填充预览表（超 20 行截断）。"""
        snapshot = self._wizard.snapshot
        self.preview_table.clear()
        if snapshot is None:
            self.preview_table.setRowCount(0)
            self.preview_table.setColumnCount(0)
            return
        rows = snapshot.rows[:PREVIEW_ROW_LIMIT]
        self.preview_table.setColumnCount(len(snapshot.fieldnames))
        self.preview_table.setRowCount(len(rows))
        self.preview_table.setHorizontalHeaderLabels(snapshot.fieldnames)
        for row_index, row in enumerate(rows):
            for column_index, name in enumerate(snapshot.fieldnames):
                value = row.get(name)
                self.preview_table.setItem(
                    row_index, column_index, QTableWidgetItem("" if value is None else value)
                )


class ExecutePage(QWizardPage):
    """第 4 步：执行导入并展示 inserted / skipped / error 汇总。"""

    def __init__(self, wizard: ImportWizard) -> None:
        super().__init__()
        self.setTitle("执行与结果")
        self.setSubTitle("点击\"开始导入\"执行本次导入（M1 数据量小，同步执行）")
        self._wizard = wizard
        self._failure_message: str | None = None
        self._duplicate_message: str | None = None

        self.run_button = QPushButton("开始导入")
        self.summary_label = QLabel("尚未执行导入")
        self.summary_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.run_button)
        layout.addWidget(self.summary_label)
        layout.addStretch(1)

        self.run_button.clicked.connect(self._execute)

    def initializePage(self) -> None:
        """进入执行页：重置上次执行状态（重复执行需重新点击开始导入）。"""
        self._wizard.import_result = None
        self._failure_message = None
        self._duplicate_message = None
        self.summary_label.setText("尚未执行导入")
        self.completeChanged.emit()

    def _execute(self) -> None:
        """按钮入口：执行导入；重复文件/前置失败弹窗告知，不继续。"""
        outcome = self._run_import()
        if outcome is None:
            if self._failure_message:
                QMessageBox.warning(self, "导入失败", self._failure_message)
            return
        if isinstance(outcome, DuplicateImportFound):
            message = self._duplicate_message or ""
            QMessageBox.warning(self, "重复导入", message)
            return
        self._fill_summary(outcome)
        self.completeChanged.emit()

    def _run_import(self) -> ImportRunSummary | DuplicateImportFound | None:
        """执行导入用例（不弹窗）：返回汇总/重复检出，前置失败返回 None。"""
        snapshot = self._wizard.snapshot
        if snapshot is None:
            return None
        import_type, mapping = self._wizard.mapping_page.collect_mapping()
        try:
            outcome = self._wizard.manager.run_import(
                path=snapshot.path, import_type=import_type, mapping=mapping
            )
        except CsvImportError as exc:
            self._failure_message = exc.message
            return None
        self._wizard.import_result = outcome
        if isinstance(outcome, DuplicateImportFound):
            self._duplicate_message = (
                f"该文件已导入过（批次 {outcome.batch_id}），本次未重复导入。"
            )
        else:
            self._duplicate_message = None
            self._fill_summary(outcome)
        return outcome

    def _fill_summary(self, summary: ImportRunSummary) -> None:
        """完成页汇总：批次状态与 行数/入库/跳过/错误。"""
        status_text = "已完成" if summary.completed else "失败（全部行校验未通过）"
        self.summary_label.setText(
            f"批次：{summary.batch_id}（{status_text}）\n"
            f"总行数：{summary.row_count}　入库：{summary.inserted}　"
            f"跳过：{summary.skipped}　错误：{summary.error_count}"
        )

    @property
    def duplicate_message(self) -> str | None:
        """最近一次重复导入提示文案（测试断言用）。"""
        return self._duplicate_message

    def isComplete(self) -> bool:
        """导入成功执行后才允许进入错误明细页。"""
        return isinstance(self._wizard.import_result, ImportRunSummary)


class ErrorPage(QWizardPage):
    """第 5 步：错误明细（数据来自 import_error 查询）。"""

    def __init__(self, wizard: ImportWizard) -> None:
        super().__init__()
        self.setTitle("错误明细")
        self.setSubTitle("校验失败的行（按修复建议修正文件后可重新导入）")
        self._wizard = wizard

        self.count_label = QLabel("")
        self.error_table = QTableWidget(0, len(ERROR_COLUMNS))
        self.error_table.setHorizontalHeaderLabels(list(ERROR_COLUMNS))
        self.error_table.horizontalHeader().setStretchLastSection(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.count_label)
        layout.addWidget(self.error_table, 1)

    def initializePage(self) -> None:
        """进入页面时按批次读取错误明细并填充表格。"""
        batch_id = self._wizard.last_batch_id
        self.error_table.setRowCount(0)
        if batch_id is None:
            self.count_label.setText("无导入结果")
            return
        records = self._wizard.manager.list_errors(batch_id)
        self.count_label.setText(f"共 {len(records)} 条错误行")
        self.error_table.setRowCount(len(records))
        for row, record in enumerate(records):
            cells = (
                str(record.row_no),
                record.field_name or "",
                record.error_code,
                record.raw_value or "",
                record.suggestion or "",
            )
            for column, text in enumerate(cells):
                self.error_table.setItem(row, column, QTableWidgetItem(text))


__all__ = [
    "ERROR_COLUMNS",
    "PREVIEW_ROW_LIMIT",
    "ImportWizard",
    "suggest_column",
]
