"""pytest-qt（offscreen）登录对话框测试。

覆盖：
- 登录成功：正确凭据 → accept → Accepted；
- 登录失败：错误密码 → 显示错误消息，对话框不关闭；
- 服务地址保存：登录成功后服务地址已写入 TokenStore；
- 取消登录：点击取消 → reject → Rejected。

后端用内存仓储控制平面（FastAPI TestClient），演示密码经
``CONTROL_PLANE_DEMO_PASSWORD`` 环境变量注入内存种子。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QDialog
from workbench.infrastructure.api_client.http_client import HttpApiClient
from workbench.infrastructure.api_client.token_store import TokenStore
from workbench.presentation.login_dialog import LoginDialog

#: 仓库根（apps/workbench-desktop/tests → apps/workbench-desktop → apps → 仓库根）
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: 控制平面服务目录（加入 sys.path 使 import app.* 可用）
_SERVICE_ROOT = str(_REPO_ROOT / "services" / "control-plane")
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

#: 演示账号登录名
DEMO_MERCHANT_LOGIN = "merchant_demo"

#: 演示密码环境变量名
DEMO_PASSWORD_ENV = "CONTROL_PLANE_DEMO_PASSWORD"

#: 演示账号固定密码（经 DEMO_PASSWORD_ENV 注入内存种子）
DEMO_PASSWORD = "unit-test-demo-pass"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def control_plane_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """内存仓储 + 固定演示密码的控制平面 FastAPI 应用。"""
    # 在导入控制平面模块前设置环境变量，确保 get_settings 读到内存仓储
    monkeypatch.setenv(DEMO_PASSWORD_ENV, DEMO_PASSWORD)
    monkeypatch.setenv("CONTROL_PLANE_REPOSITORY", "memory")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_SECRET", "unit-test-secret")
    # 不可达端口让 /health 的数据库探测快速失败（不等待超时）
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/x")

    from app.container import build_container
    from app.main import create_app
    from app.settings import REPOSITORY_MEMORY, Settings, get_settings

    # 清除 lru_cache，确保读到上面设置的环境变量
    get_settings.cache_clear()

    settings = Settings(
        APP_ENV="dev",
        AUTH_SECRET="unit-test-secret",
        CONTROL_PLANE_REPOSITORY=REPOSITORY_MEMORY,
    )
    container = build_container(settings)
    return create_app(container=container)


@pytest.fixture()
def token_store(tmp_path: Path) -> TokenStore:
    """隔离的令牌存储（tmp 下子目录）。"""
    return TokenStore(data_dir=tmp_path / "auth")


@pytest.fixture()
def api_client(
    control_plane_app: Any,
    tmp_path: Path,
) -> HttpApiClient:
    """指向内存控制平面的 HttpApiClient（注入 FastAPI TestClient 做 ASGI 桥接）。"""
    from fastapi.testclient import TestClient as FastAPITestClient

    store = TokenStore(data_dir=tmp_path / "auth")
    test_client = FastAPITestClient(control_plane_app)
    return HttpApiClient(
        base_url="http://testserver",
        token_store=store,
        client=test_client,
    )


# --------------------------------------------------------------------------
# 登录成功
# --------------------------------------------------------------------------


def test_login_success_accepts_dialog(
    qtbot,
    api_client: HttpApiClient,
) -> None:
    """正确凭据：对话框 accept，返回 Accepted。"""
    dialog = LoginDialog(api_client, default_server_url="http://testserver")
    qtbot.addWidget(dialog)

    dialog._username_input.setText(DEMO_MERCHANT_LOGIN)
    dialog._password_input.setText(DEMO_PASSWORD)
    dialog._server_input.setText("http://testserver")

    dialog._login_button.click()

    assert dialog.result() == QDialog.Accepted


# --------------------------------------------------------------------------
# 登录失败
# --------------------------------------------------------------------------


def test_login_wrong_password_shows_error(
    qtbot,
    api_client: HttpApiClient,
) -> None:
    """错误密码：显示错误消息，对话框不关闭（不 accept）。"""
    dialog = LoginDialog(api_client, default_server_url="http://testserver")
    qtbot.addWidget(dialog)

    dialog._username_input.setText(DEMO_MERCHANT_LOGIN)
    dialog._password_input.setText("wrong-password")
    dialog._server_input.setText("http://testserver")

    dialog._login_button.click()

    assert dialog.result() != QDialog.Accepted
    # offscreen 模式下 widget 未 show，isVisible 不可靠；改为断言错误文案已写入
    assert dialog._error_label.text()
    assert "错误" in dialog._error_label.text()


# --------------------------------------------------------------------------
# 服务地址保存
# --------------------------------------------------------------------------


def test_server_address_saved_to_token_store(
    qtbot,
    api_client: HttpApiClient,
    token_store: TokenStore,
) -> None:
    """登录成功后，对话框中输入的服务地址已写入 TokenStore 的 server_url。

    api_client 初始 base_url 为 http://testserver；对话框服务地址设为不同值
    http://localhost:8000，验证对话框将用户输入同步到 api_client._base_url
    并随令牌持久化（FastAPI TestClient 直接桥接 ASGI，请求不受 base_url 影响）。
    """
    dialog = LoginDialog(api_client, default_server_url="http://testserver")
    qtbot.addWidget(dialog)

    dialog._username_input.setText(DEMO_MERCHANT_LOGIN)
    dialog._password_input.setText(DEMO_PASSWORD)
    # 服务地址改为与 api_client 初始 base_url 不同的值
    dialog._server_input.setText("http://localhost:8000")

    dialog._login_button.click()

    assert dialog.result() == QDialog.Accepted
    bundle = token_store.load()
    assert bundle is not None
    assert bundle.server_url == "http://localhost:8000"


# --------------------------------------------------------------------------
# 取消登录
# --------------------------------------------------------------------------


def test_cancel_rejects_dialog(
    qtbot,
    api_client: HttpApiClient,
) -> None:
    """点击取消：对话框 reject，返回 Rejected。"""
    dialog = LoginDialog(api_client, default_server_url="http://testserver")
    qtbot.addWidget(dialog)

    dialog._cancel_button.click()

    assert dialog.result() == QDialog.Rejected
