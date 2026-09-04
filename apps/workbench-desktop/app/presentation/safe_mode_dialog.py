"""安全模式对话框（M3 Task 5，SubTask 5.1）：迁移失败后的恢复入口。

- 展示迁移错误摘要（异常类型 + 消息，只读）；
- 扫描备份目录列出可用备份（``*.db``，按修改时间倒序）；
- "从备份恢复"：调用注入的 ``restore`` 回调（组合根注入
  ``BackupService.restore`` 的安全恢复链路：先校验后原子替换，失败当前库
  零字节改动），恢复成功提示重启应用（``exec`` 返回 ``Accepted``）；
- "安全模式继续（只读）"：确认后以 ``Accepted`` 关闭且
  :attr:`continue_readonly` 为 ``True``，组合根据此进入主窗口只读模式
  （导入/分析入口禁用，无任何业务写入）；
- "退出"：以 ``Rejected`` 关闭，调用方据此结束进程。

安全模式约束（§35.9）：本对话框不做任何业务查询/写入，唯一写操作是
经备份恢复链路对本地库的受控替换。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

#: 备份文件扩展名（BackupService 经 VACUUM INTO 生成的快照文件）
BACKUP_FILE_SUFFIX = ".db"


def _backup_mtime(path: Path) -> float:
    """备份文件修改时间（stat 失败记 0，排序时沉底）。"""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


class SafeModeDialog(QDialog):
    """安全模式对话框（注入恢复回调，便于测试替换 fake）。

    :param error: 迁移阶段捕获的异常（摘要展示）。
    :param db_path: 本地库文件路径（恢复目标）。
    :param backup_dir: 备份目录（升级前备份与历史备份所在处）。
    :param restore: 恢复回调 ``(backup_path, db_path) -> RestoreResult``，
        返回对象需带 ``success`` / ``error`` 属性（鸭子类型，测试可注入 fake）。
    :param parent: Qt 父窗口。
    """

    def __init__(
        self,
        error: BaseException,
        db_path: Path,
        backup_dir: Path,
        restore: Callable[[Path, Path], object],
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self.setWindowTitle("安全模式 — 本地库迁移失败")
        self.resize(560, 440)
        self._db_path = db_path
        self._backup_dir = backup_dir
        self._restore = restore
        #: 用户选择"安全模式继续（只读）"后为 True（组合根据此进入只读主窗口）
        self.continue_readonly = False

        layout = QVBoxLayout(self)

        title = QLabel(
            "本地数据库迁移失败，应用已进入安全模式（本地库未被修改，"
            "业务功能全部停用）。可从备份恢复后重启应用。"
        )
        title.setWordWrap(True)
        layout.addWidget(title)

        # ---- 错误摘要（只读多行）----
        layout.addWidget(QLabel("迁移错误："))
        error_view = QPlainTextEdit()
        error_view.setReadOnly(True)
        error_view.setPlainText(f"{type(error).__name__}: {error}")
        error_view.setMaximumHeight(96)
        layout.addWidget(error_view)

        # ---- 可用备份列表（扫描备份目录，修改时间倒序）----
        layout.addWidget(QLabel("可用备份（选择一个进行恢复）："))
        self._backup_list = QListWidget()
        self._reload_backups()
        layout.addWidget(self._backup_list, 1)

        # ---- 操作按钮 ----
        buttons = QHBoxLayout()
        self._restore_button = QPushButton("从备份恢复")
        self._restore_button.clicked.connect(self._on_restore)
        readonly_button = QPushButton("安全模式继续（只读）")
        readonly_button.clicked.connect(self._on_continue_readonly)
        exit_button = QPushButton("退出")
        exit_button.clicked.connect(self.reject)
        buttons.addStretch(1)
        buttons.addWidget(self._restore_button)
        buttons.addWidget(readonly_button)
        buttons.addWidget(exit_button)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    # 只读继续
    # ------------------------------------------------------------------

    def _on_continue_readonly(self) -> None:
        """选择只读继续：确认后以 Accepted 关闭（continue_readonly=True）。"""
        confirm = QMessageBox.question(
            self,
            "安全模式继续（只读）",
            "迁移失败期间数据目录可能不完整，只读模式下导入与分析功能\n"
            "将被禁用（仅可浏览备份/报告等只读页面），且不产生任何写入。\n\n确定继续？",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.continue_readonly = True
        self.accept()

    # ------------------------------------------------------------------
    # 备份列表
    # ------------------------------------------------------------------

    def _reload_backups(self) -> None:
        """扫描备份目录刷新列表（目录缺失/为空时显示占位项）。"""
        self._backup_list.clear()
        for path in self._list_backup_files():
            mtime = _backup_mtime(path)
            stamp = datetime.fromtimestamp(mtime).astimezone().strftime("%Y-%m-%d %H:%M")
            try:
                size_kb = path.stat().st_size / 1024
                label = f"{path.name}（{stamp}，{size_kb:.0f} KB）"
            except OSError:
                label = f"{path.name}（{stamp}）"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self._backup_list.addItem(item)
        if self._backup_list.count() == 0:
            placeholder = QListWidgetItem(f"未发现可用备份：{self._backup_dir}")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._backup_list.addItem(placeholder)

    def _list_backup_files(self) -> list[Path]:
        """备份目录中的备份文件（*.db，修改时间倒序）。"""
        if not self._backup_dir.exists():
            return []
        files = [
            path
            for path in self._backup_dir.glob(f"*{BACKUP_FILE_SUFFIX}")
            if path.is_file()
        ]
        files.sort(key=_backup_mtime, reverse=True)
        return files

    def _selected_backup_path(self) -> Path | None:
        """当前选中的备份文件路径（未选中返回 None）。"""
        item = self._backup_list.currentItem()
        if item is None:
            return None
        raw = item.data(Qt.ItemDataRole.UserRole)
        return Path(str(raw)) if isinstance(raw, str) else None

    # ------------------------------------------------------------------
    # 恢复
    # ------------------------------------------------------------------

    def _on_restore(self) -> None:
        """恢复所选备份：先确认，再走注入的恢复链路，成功提示重启。"""
        backup_path = self._selected_backup_path()
        if backup_path is None:
            QMessageBox.information(self, "从备份恢复", "请先在列表中选择一个备份文件。")
            return
        confirm = QMessageBox.question(
            self,
            "从备份恢复",
            f"将用以下备份覆盖当前本地库：\n{backup_path.name}\n\n"
            "恢复过程先校验后替换，失败时当前库不会被修改。确定继续？",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self._restore(backup_path, self._db_path)
            ok = bool(getattr(result, "success", False))
            error = getattr(result, "error", None)
        except Exception as exc:  # noqa: BLE001 —— 恢复异常在对话框内呈现
            ok, error = False, str(exc)
        if ok:
            self.continue_readonly = False
            QMessageBox.information(
                self,
                "恢复成功",
                "本地库已恢复所选备份。\n请退出应用后重新启动（重启会补齐迁移）。",
            )
            self.accept()
        else:
            QMessageBox.critical(
                self, "恢复失败", f"恢复失败：{error}\n当前库未被修改，可重试或退出。"
            )
