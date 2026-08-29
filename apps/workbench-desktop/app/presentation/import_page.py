"""导入页（§7.2 导航实装）：打开导入向导并回显最近一次导入结果。"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from ..application.import_manager import CsvImportManager, ImportRunSummary
from .import_wizard import ImportWizard


class ImportPage(QWidget):
    """导入入口页：启动 CSV 导入向导，完成后回显最近一次汇总。"""

    def __init__(
        self,
        manager: CsvImportManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager

        title = QLabel("导入数据")
        description = QLabel(
            "通过导入向导从 CSV 文件导入主数据（SKU）或库存事件；"
            "校验失败的行会记录到错误明细（错误隔离），不影响合法行入库。"
        )
        description.setWordWrap(True)
        self.open_button = QPushButton("打开导入向导...")
        self.last_result_label = QLabel("尚未导入")
        self.last_result_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addWidget(self.open_button)
        layout.addWidget(self.last_result_label)
        layout.addStretch(1)

        self.open_button.clicked.connect(self._open_wizard)

    def _open_wizard(self) -> None:
        """模态打开导入向导；接受退出时回显最近一次导入汇总。"""
        wizard = ImportWizard(self._manager, self)
        accepted = wizard.exec() == QDialog.DialogCode.Accepted
        result = wizard.import_result
        if accepted and isinstance(result, ImportRunSummary):
            self.last_result_label.setText(
                f"最近导入：批次 {result.batch_id}"
                f"（入库 {result.inserted}、跳过 {result.skipped}、错误 {result.error_count}）"
            )
