"""pytest-qt（offscreen）StatusCard 与设备注册测试。

覆盖：
1. 在线状态展示：update_account_context 收到 ACTIVE 许可证，卡片展示正确状态与天数
2. 离线状态展示：set_offline() 后卡片展示"离线"
3. 通道变更：update_channel("sse") / update_channel("polling") 正确展示
4. 快照更新：update_snapshot 收到设备状态数据，卡片更新
5. 设备注册：auto_register_device 调用后 StatusCard 更新设备数
"""

from __future__ import annotations

from pathlib import Path

import pytest
from workbench.application.analysis_usecase import RunAnalysisUseCase
from workbench.infrastructure.db.result_store import SqlResultStore
from workbench.infrastructure.engine_adapter.providers import FakeEngineProvider
from workbench.presentation.main_window import MainWindow
from workbench.presentation.status_card import StatusCard

# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------


class MockApiClient:
    """测试用 api_client 桩（模拟 HttpApiClient 的设备/账号接口）。"""

    def __init__(
        self,
        account_me: dict | None = None,
        devices: list[dict] | None = None,
        register_result: dict | None = None,
        online: bool = True,
    ) -> None:
        self._account_me = account_me
        self._devices = devices
        self._register_result = register_result
        self.online = online
        self.register_calls: list[dict] = []

    def get_account_me(self) -> dict | None:
        return self._account_me

    def list_devices(self) -> list[dict] | None:
        return self._devices

    def register_device(self, name: str, fingerprint: str) -> dict | None:
        self.register_calls.append({"name": name, "fingerprint": fingerprint})
        return self._register_result


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def account_me_data() -> dict:
    """ACTIVE 许可证的 account/me 响应数据。"""
    return {
        "account": {
            "account_id": "ACC-001",
            "login_name": "merchant_demo",
            "role": "merchant",
            "status": "active",
        },
        "license": {
            "status": "ACTIVE",
            "days_remaining": 30,
            "max_devices": 5,
            "expires_at": "2026-09-30",
        },
    }


@pytest.fixture
def snapshot_data() -> dict:
    """SSE 快照数据。"""
    return {
        "event_id": 1,
        "generated_at": "2026-08-31T10:00:00Z",
        "license": {
            "status": "ACTIVE",
            "days_remaining": 25,
            "max_devices": 5,
        },
        "devices": [
            {"device_id": "DEV-001", "name": "PC-001", "status": "active"},
            {"device_id": "DEV-002", "name": "PC-002", "status": "active"},
        ],
    }


# --------------------------------------------------------------------------
# 1. 在线状态展示
# --------------------------------------------------------------------------


def test_update_account_context_active_license(qtbot, account_me_data: dict) -> None:
    """update_account_context 收到 ACTIVE 许可证数据，卡片展示正确状态与天数。"""
    card = StatusCard()
    qtbot.addWidget(card)

    card.update_account_context(account_me_data)

    assert "ACTIVE" in card._license_label.text()
    assert "30" in card._days_label.text()
    assert "5" in card._devices_label.text()


# --------------------------------------------------------------------------
# 2. 离线状态展示
# --------------------------------------------------------------------------


def test_set_offline_displays_offline(qtbot) -> None:
    """set_offline() 后卡片展示"离线"。"""
    card = StatusCard()
    qtbot.addWidget(card)

    card.set_offline()

    assert "离线" in card._connection_label.text()
    assert "离线" in card._channel_label.text()


# --------------------------------------------------------------------------
# 3. 通道变更
# --------------------------------------------------------------------------


def test_update_channel_sse(qtbot) -> None:
    """update_channel("sse") 正确展示实时通道与在线状态。"""
    card = StatusCard()
    qtbot.addWidget(card)

    card.update_channel("sse")

    assert "实时" in card._channel_label.text()
    assert "在线" in card._connection_label.text()


def test_update_channel_polling(qtbot) -> None:
    """update_channel("polling") 正确展示轮询降级与在线状态。"""
    card = StatusCard()
    qtbot.addWidget(card)

    card.update_channel("polling")

    assert "轮询降级" in card._channel_label.text()
    assert "在线" in card._connection_label.text()


# --------------------------------------------------------------------------
# 4. 快照更新
# --------------------------------------------------------------------------


def test_update_snapshot_updates_devices(qtbot, snapshot_data: dict) -> None:
    """update_snapshot 收到设备状态数据，卡片更新设备数与最后连接时间。"""
    card = StatusCard()
    qtbot.addWidget(card)

    card.update_snapshot(snapshot_data)

    assert "2" in card._devices_label.text()  # 2 台设备
    assert "2026-08-31" in card._last_seen_label.text()


# --------------------------------------------------------------------------
# 5. 设备注册
# --------------------------------------------------------------------------


def test_auto_register_device_updates_card(
    qtbot,
    fake_provider: FakeEngineProvider,
    store: SqlResultStore,
    golden_input: Path,
    account_me_data: dict,
) -> None:
    """auto_register_device 调用后 StatusCard 更新设备数。"""
    window = MainWindow(RunAnalysisUseCase(fake_provider, store, golden_input), store)
    qtbot.addWidget(window)

    # 注册成功后 list_devices 返回的设备列表（含新注册设备）
    devices = [
        {"device_id": "DEV-001", "name": "PC-001", "status": "active"},
        {"device_id": "DEV-002", "name": "PC-002", "status": "active"},
    ]
    api_client = MockApiClient(
        account_me=account_me_data,
        devices=devices,
        register_result={"device_id": "DEV-002", "name": "PC-002"},
    )

    window.auto_register_device(api_client)

    # 验证 register_device 被调用且指纹为 32 字符
    assert len(api_client.register_calls) == 1
    assert api_client.register_calls[0]["name"]
    assert len(api_client.register_calls[0]["fingerprint"]) == 32

    # StatusCard 应显示 2 台设备 / 上限 5
    assert "2" in window.status_card._devices_label.text()
    assert "5" in window.status_card._devices_label.text()
    assert "ACTIVE" in window.status_card._license_label.text()


# --------------------------------------------------------------------------
# 6. 待同步数（M3 Task 5：SyncWorker.sync_progress → StatusCard）
# --------------------------------------------------------------------------


def test_update_sync_pending_shows_count(qtbot) -> None:
    """update_sync_pending 更新待同步数展示。"""
    card = StatusCard()
    qtbot.addWidget(card)

    card.update_sync_pending(7)

    assert card._sync_pending_label.text() == "待同步：7"


def test_sync_progress_signal_updates_status_card(qtbot) -> None:
    """SyncWorker.sync_progress(applied, failed, pending) → StatusCard 待同步数。

    经真实 Qt 信号（QThread 类信号）触发 update_sync_pending，验证总览页
    接线所依赖的信号-槽链路（pending 参数即卡片待同步数）。
    """
    from workbench.workers.sync_worker import SyncWorker

    card = StatusCard()
    qtbot.addWidget(card)
    worker = SyncWorker(api_client=object(), session_factory=object(), device_id="dev-x")
    worker.sync_progress.connect(
        lambda applied, failed, pending: card.update_sync_pending(pending)
    )

    with qtbot.waitSignal(worker.sync_progress) as blocker:
        worker.sync_progress.emit(1, 0, 7)

    assert list(blocker.args) == [1, 0, 7]
    assert card._sync_pending_label.text() == "待同步：7"
