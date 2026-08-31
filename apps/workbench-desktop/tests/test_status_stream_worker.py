"""StatusStreamWorker 测试：SSE 帧解析、降级轮询、离线、stop。

使用 pytest-qt（offscreen 模式），通过 httpx.MockTransport 模拟 SSE 响应/失败，
FakeApiClient 模拟 api_client 的 stream_url / get_snapshot / access_token。

覆盖场景：
1. SSE 帧解析：snapshot 事件 + data JSON → snapshot_received 信号；
2. SSE 失败降级轮询：连接失败 → channel_changed("polling")；
3. 轮询成功：get_snapshot() 返回数据 → snapshot_received 信号；
4. 全部失败离线：SSE 和轮询都失败 → channel_changed("offline")；
5. stop() 能正确停止线程。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from workbench.workers.status_stream_worker import StatusStreamWorker

# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------


class FakeApiClient:
    """测试用 api_client 桩。"""

    def __init__(
        self,
        snapshot: dict | None = None,
        access_token: str | None = "test-token",
        online: bool = True,
    ) -> None:
        self._snapshot = snapshot
        self._access_token = access_token
        self.online = online

    @property
    def access_token(self) -> str | None:
        return self._access_token

    def stream_url(self) -> str:
        return "http://testserver/api/v1/events/stream"

    def get_snapshot(self) -> dict | None:
        return self._snapshot


def _snapshot_frame(data: dict, event: str = "snapshot") -> bytes:
    """构造单帧 SSE snapshot 事件字节。"""
    payload = json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n".encode()


def _keepalive() -> bytes:
    """构造保活注释行。"""
    return b": keepalive\n\n"


def _refuse_handler(request: httpx.Request) -> httpx.Response:
    """模拟连接被拒的 MockTransport 处理器。"""
    raise httpx.ConnectError("连接被拒绝", request=request)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def make_worker(qtbot):
    """工厂：创建 StatusStreamWorker，测试结束自动 stop+wait。"""
    created: list[StatusStreamWorker] = []

    def _factory(
        api_client: Any,
        poll_interval: int = 1,
        transport: httpx.BaseTransport | None = None,
    ) -> StatusStreamWorker:
        w = StatusStreamWorker(
            api_client,
            poll_interval=poll_interval,
            transport=transport,
        )
        created.append(w)
        return w

    yield _factory
    for w in created:
        w.stop()
        w.wait(5000)


# --------------------------------------------------------------------------
# 1. SSE 帧解析
# --------------------------------------------------------------------------


def test_sse_frame_parsing_emits_snapshot(qtbot, make_worker):
    """SSE 帧解析：收到 snapshot 事件 + data JSON，验证 snapshot_received 信号。

    同时验证：
    - 保活注释行（``: keepalive``）被正确忽略；
    - SSE 连接成功时 channel_changed 发出 "sse"。
    """
    snapshot_data = {"event_id": "EVT-001", "license": {"status": "ACTIVE"}}
    # SSE 流：保活行 + snapshot 事件 + 末尾保活
    sse_body = _keepalive() + _snapshot_frame(snapshot_data) + _keepalive()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse_body)

    transport = httpx.MockTransport(handler)
    api_client = FakeApiClient(snapshot=None)
    worker = make_worker(api_client, poll_interval=1, transport=transport)

    snapshots: list[dict] = []
    channels: list[str] = []
    worker.snapshot_received.connect(snapshots.append)
    worker.channel_changed.connect(channels.append)

    worker.start()
    qtbot.waitUntil(lambda: len(snapshots) > 0, timeout=5000)

    assert snapshots[0] == snapshot_data
    assert "sse" in channels


# --------------------------------------------------------------------------
# 2. SSE 失败降级轮询
# --------------------------------------------------------------------------


def test_sse_failure_falls_back_to_polling(qtbot, make_worker):
    """SSE 连接失败：验证 channel_changed 发出 "polling" 信号。"""
    transport = httpx.MockTransport(_refuse_handler)
    api_client = FakeApiClient(snapshot=None)
    worker = make_worker(api_client, poll_interval=1, transport=transport)

    channels: list[str] = []
    worker.channel_changed.connect(channels.append)

    worker.start()
    qtbot.waitUntil(lambda: "polling" in channels, timeout=5000)

    assert "polling" in channels


# --------------------------------------------------------------------------
# 3. 轮询成功
# --------------------------------------------------------------------------


def test_polling_emits_snapshot(qtbot, make_worker):
    """轮询成功：get_snapshot() 返回数据，验证 snapshot_received 信号。"""
    transport = httpx.MockTransport(_refuse_handler)
    snapshot_data = {"event_id": "EVT-003", "license": {"status": "ACTIVE"}}
    api_client = FakeApiClient(snapshot=snapshot_data)
    worker = make_worker(api_client, poll_interval=1, transport=transport)

    snapshots: list[dict] = []
    worker.snapshot_received.connect(snapshots.append)

    worker.start()
    qtbot.waitUntil(lambda: len(snapshots) > 0, timeout=5000)

    assert snapshots[0] == snapshot_data


# --------------------------------------------------------------------------
# 4. 全部失败离线
# --------------------------------------------------------------------------


def test_all_fail_emits_offline(qtbot, make_worker):
    """SSE 和轮询都失败：验证 channel_changed 发出 "offline" 信号。"""
    transport = httpx.MockTransport(_refuse_handler)
    api_client = FakeApiClient(snapshot=None, online=False)
    worker = make_worker(api_client, poll_interval=1, transport=transport)

    channels: list[str] = []
    worker.channel_changed.connect(channels.append)

    worker.start()
    qtbot.waitUntil(lambda: "offline" in channels, timeout=5000)

    assert "offline" in channels


# --------------------------------------------------------------------------
# 5. stop() 能正确停止线程
# --------------------------------------------------------------------------


def test_stop_terminates_thread(qtbot, make_worker):
    """stop() 后线程应在合理时间内退出。"""
    transport = httpx.MockTransport(_refuse_handler)
    api_client = FakeApiClient(snapshot=None)
    # 长 poll_interval 让线程进入 offline 睡眠，便于在此期间调用 stop()
    worker = make_worker(api_client, poll_interval=60, transport=transport)

    channels: list[str] = []
    worker.channel_changed.connect(channels.append)

    worker.start()
    qtbot.waitUntil(lambda: "offline" in channels, timeout=5000)

    worker.stop()
    assert worker.wait(3000), "线程应在 stop() 后退出"
