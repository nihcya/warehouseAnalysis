"""系统托盘图标：主窗口关闭后驻留后台，托盘提供"显示 / 退出"入口。

- 主窗口 closeEvent 改为 hide()（驻留托盘，不退出应用）；
- 仅托盘"退出"动作调用 ``MainWindow.quit_application``：先停 Agent 与
  SSE 后台线程，再 ``QApplication.quit()``；
- 配置验签失败经 ``config_rejected`` 信号转为托盘气泡警告。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from .main_window import MainWindow

#: 托盘主色（与项目视觉一致）
_PRIMARY_COLOR = "#173B5F"


def _make_icon() -> QIcon:
    """生成 16×16 圆角方块图标（不依赖外部资源文件）。"""
    pix = QPixmap(16, 16)
    pix.fill(QColor("transparent"))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(_PRIMARY_COLOR))
    painter.setBrush(QColor(_PRIMARY_COLOR))
    painter.drawRoundedRect(1, 1, 14, 14, 4, 4)
    painter.end()
    return QIcon(pix)


class TrayIcon(QSystemTrayIcon):
    """工作台托盘图标：后台驻留入口与配置告警通知。"""

    #: 配置验签失败 / 载荷非法（转发自 AgentWorker，用于托盘气泡警告）
    config_rejected = Signal(str)

    def __init__(self, window: MainWindow, parent: QWidget | None = None) -> None:
        super().__init__(_make_icon(), parent)
        self._window = window
        self.setToolTip("仓库品类分析工作台")

        menu = QMenu()
        show_action = QAction("显示", menu)
        show_action.triggered.connect(self._show_window)
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(window.quit_application)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.setContextMenu(menu)

        self.activated.connect(self._on_activated)
        self.config_rejected.connect(self._notify_config_rejected)

    def _show_window(self) -> None:
        """恢复并前置主窗口。"""
        self._window.showNormal()
        self._window.raise_()
        self._window.activateWindow()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """左键单击托盘图标切换主窗口显示。"""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_window()

    def _notify_config_rejected(self, reason: str) -> None:
        """配置验签失败托盘气泡警告。"""
        self.showMessage("工作台 Agent", reason, QSystemTrayIcon.MessageIcon.Warning, 5000)
