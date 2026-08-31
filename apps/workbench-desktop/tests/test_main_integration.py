"""组合根切换与离线模式整合测试（Task 5）。

覆盖：
- ``create_api_client()`` 组合根分支：默认返回 ``HttpApiClient``（base_url 来自
  ``WORKBENCH_API_URL``），``WORKBENCH_OFFLINE=1`` 返回 ``OfflineApiClient``；
- 离线降级：控制平面不可达时 ``online=False``，MainWindow 离线模式下本地功能
  （导入/分析/报告/备份）不受阻；
- 启动流程：有效令牌静默放行、无令牌弹出登录对话框（mock 拦截）。

测试不启动事件循环（不调用 ``main()`` / ``app.exec()``）；需要 QWidget 的场景
经 pytest-qt 的 ``qtbot`` 提供的 QApplication 构造，与既有 UI 测试同口径。
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from PySide6.QtWidgets import QDialog, QLabel
from workbench.application.analysis_usecase import RunAnalysisUseCase
from workbench.application.import_manager import CsvImportManager
from workbench.infrastructure.api_client.client import OfflineApiClient
from workbench.infrastructure.api_client.http_client import HttpApiClient
from workbench.infrastructure.api_client.token_store import TokenStore
from workbench.main import (
    create_api_client,
    create_backup_manager,
    create_report_export_manager,
)
from workbench.presentation.main_window import MainWindow

# --------------------------------------------------------------------------
# 组合根分支：create_api_client()
# --------------------------------------------------------------------------


def test_create_api_client_returns_http_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """默认分支：返回 HttpApiClient，base_url 来自 WORKBENCH_API_URL。"""
    monkeypatch.setenv("WORKBENCH_API_URL", "http://test-server:9999")
    # 令牌存储目录跟随 WORKBENCH_DATA_DIR（与本地库相同的数据目录逻辑）
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path / "tokens"))
    monkeypatch.delenv("WORKBENCH_OFFLINE", raising=False)

    client = create_api_client()

    assert isinstance(client, HttpApiClient)
    assert client._base_url == "http://test-server:9999"
    assert client._token_store.path.parent == tmp_path / "tokens"


def test_create_api_client_default_base_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """未设置 WORKBENCH_API_URL 时使用默认地址 http://localhost:8000。"""
    monkeypatch.delenv("WORKBENCH_API_URL", raising=False)
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path / "tokens"))
    monkeypatch.delenv("WORKBENCH_OFFLINE", raising=False)

    client = create_api_client()

    assert isinstance(client, HttpApiClient)
    assert client._base_url == "http://localhost:8000"


def test_create_api_client_offline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """WORKBENCH_OFFLINE=1 时返回离线占位客户端。"""
    monkeypatch.setenv("WORKBENCH_OFFLINE", "1")

    client = create_api_client()

    assert isinstance(client, OfflineApiClient)
    assert client.online is False


# --------------------------------------------------------------------------
# 离线降级
# --------------------------------------------------------------------------


def test_unreachable_api_client_offline(tmp_path: Path) -> None:
    """控制平面不可达：get_account_me 返回 None，online=False，health 给出离线占位。

    用 MockTransport 模拟连接被拒，避免依赖真实端口状态（与既有离线测试同口径）。
    """
    token_store = TokenStore(data_dir=tmp_path)

    def _refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("连接被拒绝", request=request)

    mock_client = httpx.Client(
        base_url="http://unreachable.test",
        transport=httpx.MockTransport(_refuse),
    )
    client = HttpApiClient(
        base_url="http://unreachable.test",
        token_store=token_store,
        client=mock_client,
    )

    assert client.get_account_me() is None
    assert client.online is False
    assert client.health()["error"]["code"] == "CLIENT_OFFLINE"


def test_main_window_offline_mode_local_features_unblocked(
    qtbot,
    fake_provider,
    store,
    session_factory,
    data_dir,
    golden_input,
) -> None:
    """离线模式下本地功能（导入/分析/报告/备份）页面齐全且不受阻。"""
    import_manager = CsvImportManager(session_factory)
    report_manager = create_report_export_manager(session_factory, data_dir)
    backup_manager = create_backup_manager(session_factory, data_dir)

    window = MainWindow(
        RunAnalysisUseCase(fake_provider, store, golden_input),
        store,
        api_client=OfflineApiClient(),
        import_manager=import_manager,
        report_manager=report_manager,
        backup_manager=backup_manager,
    )
    qtbot.addWidget(window)

    # 状态栏明示离线
    status_texts = " ".join(
        label.text() for label in window.statusBar().findChildren(QLabel)
    )
    assert "网络：离线" in status_texts

    # 本地页面均已创建：导入/分析/报告/备份（离线不阻断本地操作）
    assert window.import_page is not None
    assert window.analysis_page is not None
    assert window.report_page is not None
    assert window.backup_page is not None


# --------------------------------------------------------------------------
# 启动流程：check_login_on_startup
# --------------------------------------------------------------------------


class _FakeApiClient:
    """测试用 api_client 桩：get_account_me 返回预设值，模拟令牌有效/无效。

    check_login_on_startup 仅依赖 ``get_account_me``（与 LoginDialog 构造），
    故桩只实现该方法即可；其余方法供 refresh_status / auto_register_device 兜底。
    """

    def __init__(self, account_data: dict | None = None) -> None:
        self._account_data = account_data
        self.online = account_data is not None

    def get_account_me(self) -> dict | None:
        return self._account_data

    def login(self, username: str, password: str) -> dict | None:
        return None

    def register_device(self, name: str, fingerprint: str) -> dict | None:
        return None

    def list_devices(self) -> list[dict] | None:
        return None

    def stream_url(self) -> str:
        return "http://testserver/api/v1/events/stream"

    @property
    def access_token(self) -> str | None:
        return "test-token"


def test_check_login_silent_when_token_valid(
    qtbot,
    fake_provider,
    store,
    golden_input,
) -> None:
    """有效令牌：get_account_me 成功 → 静默放行，不弹对话框。"""
    api_client = _FakeApiClient(account_data={"merchant_id": "M1", "login": "demo"})
    window = MainWindow(RunAnalysisUseCase(fake_provider, store, golden_input), store)
    qtbot.addWidget(window)

    assert window.check_login_on_startup(api_client) is True


def test_check_login_shows_dialog_when_no_token(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    fake_provider,
    store,
    golden_input,
) -> None:
    """无令牌：get_account_me 返回 None → 弹出登录对话框（mock 拦截为取消 → 离线）。"""
    import workbench.presentation.main_window as mw_mod

    class _StubLoginDialog:
        """登录对话框桩：exec 立即返回取消，不真正显示。"""

        def __init__(self, *args, **kwargs) -> None:
            _StubLoginDialog.constructed = True

        def exec(self) -> int:
            return QDialog.DialogCode.Rejected

    _StubLoginDialog.constructed = False
    monkeypatch.setattr(mw_mod, "LoginDialog", _StubLoginDialog)

    api_client = _FakeApiClient(account_data=None)
    window = MainWindow(RunAnalysisUseCase(fake_provider, store, golden_input), store)
    qtbot.addWidget(window)

    assert window.check_login_on_startup(api_client) is False
    assert _StubLoginDialog.constructed is True
