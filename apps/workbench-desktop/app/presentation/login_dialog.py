"""登录对话框：启动时凭证录入与服务地址配置。

- UI：用户名、密码（密文回显）、服务地址（默认 http://localhost:8000）、
  登录按钮、取消按钮、错误提示标签；
- 登录：调用注入的 ``HttpApiClient.login``，成功则 ``accept()``（令牌已由
  HttpApiClient 自动持久化），失败时按在线状态展示错误提示；
- 服务地址：登录前同步到 ``HttpApiClient._base_url``，确保令牌持久化时
  ``server_url`` 与用户输入一致。

展示层运行时不 import infrastructure（HttpApiClient 仅用于类型标注）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from ..infrastructure.api_client.http_client import HttpApiClient

#: 默认控制平面服务地址
DEFAULT_SERVER_URL = "http://localhost:8000"


class LoginDialog(QDialog):
    """登录对话框：录入凭据并连接控制平面。"""

    def __init__(
        self,
        api_client: HttpApiClient,
        default_server_url: str = DEFAULT_SERVER_URL,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("登录")
        self.setMinimumWidth(360)
        self._api_client = api_client

        # ---- 表单字段 ----
        self._server_input = QLineEdit(default_server_url)
        self._username_input = QLineEdit()
        self._username_input.setPlaceholderText("请输入用户名")
        self._password_input = QLineEdit()
        self._password_input.setPlaceholderText("请输入密码")
        self._password_input.setEchoMode(QLineEdit.EchoMode.Password)

        form = QFormLayout()
        form.addRow("服务地址", self._server_input)
        form.addRow("用户名", self._username_input)
        form.addRow("密码", self._password_input)

        # ---- 错误提示（默认隐藏）----
        self._error_label = QLabel()
        self._error_label.setStyleSheet("color: #c0392b;")
        self._error_label.setVisible(False)

        # ---- 按钮 ----
        self._login_button = QPushButton("登录")
        self._login_button.setDefault(True)
        self._cancel_button = QPushButton("取消")

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self._login_button)
        buttons.addWidget(self._cancel_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._error_label)
        layout.addLayout(buttons)

        # ---- 信号 ----
        self._login_button.clicked.connect(self._on_login)
        self._cancel_button.clicked.connect(self.reject)
        self._username_input.returnPressed.connect(self._on_login)
        self._password_input.returnPressed.connect(self._on_login)

    def _on_login(self) -> None:
        """登录：校验非空 → 同步服务地址 → 调用 api_client.login → 成功 accept。"""
        username = self._username_input.text().strip()
        password = self._password_input.text()
        server_url = self._server_input.text().strip()

        if not username or not password:
            self._show_error("请输入用户名和密码。")
            return
        if not server_url:
            self._show_error("请输入服务地址。")
            return

        # 同步服务地址到 api_client（影响令牌持久化的 server_url 字段）
        self._api_client._base_url = server_url

        data = self._api_client.login(username, password)
        if data is not None:
            # 令牌已由 HttpApiClient._save_tokens 自动持久化
            self.accept()
            return

        # 失败：按在线状态区分错误提示
        if not self._api_client.online:
            self._show_error("无法连接服务地址，请检查地址与网络后重试。")
        else:
            self._show_error("用户名或密码错误，请重试。")

    def _show_error(self, message: str) -> None:
        """显示错误提示并聚焦密码框便于重输。"""
        self._error_label.setText(message)
        self._error_label.setVisible(True)
        self._password_input.setFocus()
        self._password_input.selectAll()
