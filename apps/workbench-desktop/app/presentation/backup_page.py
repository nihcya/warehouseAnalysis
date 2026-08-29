"""备份页（M1 Task 9.2）：手动备份 + 备份列表 + 恢复（二次确认）。

- 手动备份：VACUUM INTO 全量备份 + SHA-256 + backup_record 登记（VERIFIED）；
- 备份列表：时间 / 类型 / 大小 / 状态；
- 恢复：``QMessageBox.question`` 二次确认 → 安全恢复流程
  （先校验后原子替换，失败当前库零改动）→ 结果消息回显。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..application.backup_manager import BackupManager

#: 备份列表格列（中文表头）
BACKUP_COLUMNS = ("备份编号", "备份时间", "类型", "大小", "状态", "验证时间")

#: 备份类型中文展示
_TYPE_TEXT = {"MANUAL": "手动", "AUTO": "自动"}


def _size_text(size_bytes: int) -> str:
    """大小列文本（人类可读，保留整数 KB/MB）。"""
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes // (1024 * 1024)} MB"
    if size_bytes >= 1024:
        return f"{size_bytes // 1024} KB"
    return f"{size_bytes} B"


class BackupPage(QWidget):
    """备份与恢复页。"""

    def __init__(
        self,
        manager: BackupManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager

        title = QLabel("备份与恢复")
        description = QLabel(
            "手动备份生成当前数据库的完整快照（VACUUM INTO）并登记校验值；"
            "恢复前会先校验备份完整性与 schema 版本，通过后原子替换当前库，"
            "失败时当前库保持原样不做任何改动。"
        )
        description.setWordWrap(True)

        self.backup_button = QPushButton("手动备份")
        self.refresh_button = QPushButton("刷新列表")
        self.restore_button = QPushButton("恢复此备份...")

        self.backup_table = QTableWidget(0, len(BACKUP_COLUMNS))
        self.backup_table.setHorizontalHeaderLabels(list(BACKUP_COLUMNS))
        self.backup_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.backup_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.backup_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.backup_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.backup_table.horizontalHeader().setStretchLastSection(True)

        self.status_label = QLabel("尚未备份")
        self.status_label.setWordWrap(True)

        actions = QHBoxLayout()
        actions.addWidget(self.backup_button)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.restore_button)
        actions.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addLayout(actions)
        layout.addWidget(self.backup_table, 1)
        layout.addWidget(self.status_label)

        self.backup_button.clicked.connect(self._run_backup)
        self.refresh_button.clicked.connect(self.refresh_backups)
        self.restore_button.clicked.connect(self._restore_selected)

        self.refresh_backups()

    # ------------------------------------------------------------------
    # 列表
    # ------------------------------------------------------------------

    def refresh_backups(self) -> None:
        """重载备份记录列表（新 → 旧）。"""
        backups = self._manager.list_backups()
        self.backup_table.setRowCount(len(backups))
        for row, record in enumerate(backups):
            cells = (
                str(record["backup_id"]),
                str(record["created_at"]),
                _TYPE_TEXT.get(str(record["backup_type"]), str(record["backup_type"])),
                _size_text(int(record["size_bytes"])),
                str(record["status"]),
                str(record["verified_at"] or "—"),
            )
            for column, text in enumerate(cells):
                self.backup_table.setItem(row, column, QTableWidgetItem(text))

    def _selected_backup_id(self) -> str | None:
        """当前选中行的 backup_id；未选中返回 None 并提示。"""
        row = self.backup_table.currentRow()
        if row < 0:
            self.status_label.setText("请先在列表中选择一个备份")
            return None
        item = self.backup_table.item(row, 0)
        return item.text() if item is not None else None

    # ------------------------------------------------------------------
    # 备份与恢复
    # ------------------------------------------------------------------

    def _run_backup(self) -> None:
        """执行手动备份并回显结果。"""
        status = self._manager.run_manual_backup()
        self.status_label.setText(status.message)
        self.refresh_backups()

    def _restore_selected(self) -> None:
        """恢复选中备份：二次确认 → 安全恢复 → 结果消息 → 刷新列表。"""
        backup_id = self._selected_backup_id()
        if backup_id is None:
            return
        answer = QMessageBox.question(
            self,
            "确认恢复",
            "确定要用该备份替换当前数据库吗？\n"
            "恢复前会自动安全备份当前库；校验失败时当前库不做任何改动。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.status_label.setText("已取消恢复")
            return
        status = self._manager.restore(backup_id)
        self.status_label.setText(status.message)
        # 恢复后库内容已切换（备份记录随恢复的库变化，且安全备份
        # 记录会补登记进恢复后的库），刷新列表以反映真实状态
        self.refresh_backups()
