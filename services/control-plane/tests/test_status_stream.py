"""状态流测试（M2）：SSE 主通道、Last-Event-ID 续传与 30 秒轮询降级。

DECISIONS.md D-006：SSE（Last-Event-ID 续传，失败后 30 秒轮询降级）。

**为什么不用 TestClient 读 SSE**：``httpx`` 的 ASGITransport 与 Starlette
TestClient 都会把响应缓冲到"整体结束"才返回，而 SSE 是永不结束的长连接，
两者组合会直接挂住。因此：

- 帧序列语义（首帧快照、续传补发、实时推送、租户过滤、保活）直接驱动
  ``app.api.v1.routes.sse_event_stream`` 生成器逐帧断言；
- 传输层（状态码、``text/event-stream``、降级响应头）用真实 uvicorn 服务器
  做一次冒烟验证（见 ``test_live_stream_headers``）。

覆盖：

- ``GET /events/snapshot``：轮询降级入口，与 SSE 首帧同构；
- SSE 生成器：首帧快照、续传、实时事件、租户隔离、保活帧；
- 事件中心：环形缓冲、订阅者回收、慢消费者不阻塞发布。
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from app.api.v1.routes import SSE_EVENT_SNAPSHOT, sse_event_stream
from app.container import Container
from app.infrastructure.realtime.hub import DEFAULT_BUFFER_SIZE, parse_last_event_id
from app.main import create_app
from conftest import DEMO_MERCHANT_LOGIN, TEST_PASSWORD, build_test_container, login_token
from fastapi.testclient import TestClient

#: 单条异步用例的等待上限（秒）
ASYNC_TIMEOUT_SECONDS = 6.0


async def _collect(
    stream: AsyncIterator[str],
    wanted: int,
    *,
    timeout: float = ASYNC_TIMEOUT_SECONDS,
) -> list[str]:
    """从 SSE 生成器收集若干帧后关闭生成器（模拟客户端断开）。"""

    async def reader() -> list[str]:
        frames: list[str] = []
        async for frame in stream:
            frames.append(frame)
            if len(frames) >= wanted:
                break
        return frames

    try:
        return await asyncio.wait_for(reader(), timeout=timeout)
    finally:
        await stream.aclose()


def _payloads(frames: list[str]) -> list[dict[str, Any]]:
    """从若干帧中解析 data 行载荷。"""
    return [
        json.loads(line[len("data: ") :])
        for frame in frames
        for line in frame.splitlines()
        if line.startswith("data: ")
    ]


# ------------------------- 轮询降级入口 -------------------------


def test_snapshot_endpoint_returns_current_state(client: TestClient) -> None:
    """轮询降级入口：返回许可证、设备列表与当前事件 ID。"""
    token = login_token(client, DEMO_MERCHANT_LOGIN)
    headers = {"Authorization": f"Bearer {token}"}
    client.post(
        "/api/v1/devices/register",
        json={"name": "前台", "fingerprint": "fp-stream-1"},
        headers=headers,
    )

    resp = client.get("/api/v1/events/snapshot", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["license"]["status"] == "ACTIVE"
    assert [device["fingerprint"] for device in data["devices"]] == ["fp-stream-1"]
    # M3 才上报的字段：如实为空，不伪造成 0
    assert data["pending_sync_count"] is None
    assert data["tasks"] == []
    assert data["event_id"] >= 1


def test_snapshot_requires_authentication(client: TestClient) -> None:
    """状态快照需要鉴权：无令牌 401。"""
    assert client.get("/api/v1/events/snapshot").status_code == 401


def test_snapshot_has_same_shape_as_sse_first_frame(container: Container) -> None:
    """SSE 首帧与轮询快照同构：降级后 UI 无需改解析逻辑。"""
    frames = asyncio.run(_collect(sse_event_stream(container.hub, container, "tnt_demo"), 1))
    payloads = _payloads(frames)
    assert len(payloads) == 1
    assert set(payloads[0]) == {
        "event_id",
        "generated_at",
        "license",
        "devices",
        "pending_sync_count",
        "tasks",
    }


# ------------------------- SSE 帧序列 -------------------------


def test_stream_sends_snapshot_first(container: Container) -> None:
    """SSE：首帧为 snapshot，带事件 ID（供 Last-Event-ID 续传）。"""
    frames = asyncio.run(_collect(sse_event_stream(container.hub, container, "tnt_demo"), 1))
    assert frames and frames[0].startswith("id: ")
    assert f"event: {SSE_EVENT_SNAPSHOT}" in frames[0]


def test_stream_pushes_live_events(container: Container) -> None:
    """SSE：连接期间发布的事件实时出现在流里。"""
    stream = sse_event_stream(
        container.hub, container, "tnt_demo", poll_interval=0.05
    )
    threading.Timer(
        0.3, lambda: container.hub.publish("tnt_demo", "test.ping", {"hello": "world"})
    ).start()
    frames = asyncio.run(_collect(stream, 2))
    assert any("event: test.ping" in frame for frame in frames)
    assert {"hello": "world"} in _payloads(frames)


def test_stream_resumes_from_last_event_id(container: Container) -> None:
    """断线续传：带 Last-Event-ID 重连时先补发该 ID 之后的缓冲事件。"""
    container.hub.publish("tnt_demo", "device.registered", {"name": "first"})
    first_id = container.hub.latest_event_id
    container.hub.publish("tnt_demo", "device.registered", {"name": "second"})

    stream = sse_event_stream(
        container.hub, container, "tnt_demo", resume_from=first_id, poll_interval=0.05
    )
    frames = asyncio.run(_collect(stream, 2))
    replayed = [frame for frame in frames if "event: device.registered" in frame]
    assert replayed, f"未补发缓冲事件：{frames}"
    assert [payload["name"] for payload in _payloads(replayed)] == ["second"]


def test_stream_without_last_event_id_skips_backlog(container: Container) -> None:
    """未带 Last-Event-ID：不补发历史事件，直接给当前快照。"""
    container.hub.publish("tnt_demo", "device.registered", {"name": "first"})
    stream = sse_event_stream(container.hub, container, "tnt_demo", poll_interval=0.05)
    frames = asyncio.run(_collect(stream, 1))
    assert "device.registered" not in frames[0]


def test_stream_hides_other_tenants_events(container: Container) -> None:
    """租户隔离：其它商户的事件不会推送到本商户的流。"""
    container.hub.publish("tnt_other", "device.registered", {"name": "别人的设备"})
    stream = sse_event_stream(container.hub, container, "tnt_demo", resume_from=0)
    frames = asyncio.run(_collect(stream, 1))
    assert frames
    assert all("别人的设备" not in frame for frame in frames)


def test_stream_emits_keepalive_when_idle(container: Container) -> None:
    """空闲超时发送保活帧（注释行），避免中间层断开连接。"""
    stream = sse_event_stream(
        container.hub,
        container,
        "tnt_demo",
        keepalive_seconds=0.05,
        poll_interval=0.01,
    )
    frames = asyncio.run(_collect(stream, 2))
    assert any(frame.startswith(": keepalive") for frame in frames)


def test_closing_stream_unsubscribes(container: Container) -> None:
    """生成器关闭后订阅者被注销，不泄漏连接。"""
    stream = sse_event_stream(container.hub, container, "tnt_demo")
    asyncio.run(_collect(stream, 1))
    assert len(container.hub._subscribers) == 0  # type: ignore[attr-defined]


# ------------------------- 传输层冒烟（真实服务器） -------------------------


@pytest.fixture(scope="module")
def live_server_url() -> Iterator[str]:
    """启动真实 uvicorn 服务器（模块级复用，自带容器），返回基地址。"""
    port = _free_port()
    config = uvicorn.Config(
        create_app(build_test_container()),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:  # pragma: no cover —— 环境异常时快速失败
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("uvicorn 未能在 10 秒内启动")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _free_port() -> int:
    """取一个空闲端口（避免与本机其它服务冲突）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_live_stream_headers_and_first_frame(live_server_url: str) -> None:
    """真实 HTTP 传输：200 + text/event-stream + 降级响应头 + 首帧快照。"""
    deadline = time.monotonic() + ASYNC_TIMEOUT_SECONDS

    async def read_first_frame() -> tuple[int, httpx.Headers, str]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            login = await client.post(
                f"{live_server_url}/api/v1/auth/login",
                json={"username": DEMO_MERCHANT_LOGIN, "password": TEST_PASSWORD},
            )
            token = login.json()["data"]["tokens"]["access_token"]
            async with client.stream(
                "GET",
                f"{live_server_url}/api/v1/events/stream",
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                first = ""
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        first = line
                        break
                    if time.monotonic() > deadline:  # pragma: no cover
                        break
                return response.status_code, response.headers, first

    status, response_headers, first_frame = asyncio.run(read_first_frame())
    assert status == 200
    assert response_headers["content-type"].startswith("text/event-stream")
    assert response_headers["X-Polling-Fallback"] == "/api/v1/events/snapshot"
    assert response_headers["X-Polling-Interval"] == "30"
    assert first_frame == f"event: {SSE_EVENT_SNAPSHOT}"


# ------------------------- 事件中心 -------------------------


def test_parse_last_event_id_accepts_positive_integers_only() -> None:
    """Last-Event-ID 解析：正整数有效，空值与非数字返回 None。"""
    assert parse_last_event_id("42") == 42
    assert parse_last_event_id("") is None
    assert parse_last_event_id(None) is None
    assert parse_last_event_id("abc") is None
    assert parse_last_event_id("0") is None


def test_ring_buffer_keeps_latest_events(container: Container) -> None:
    """环形缓冲：超过容量后只保留最近事件，事件 ID 单调递增。"""
    for index in range(DEFAULT_BUFFER_SIZE + 5):
        container.hub.publish("tnt_demo", "test.fill", {"index": index})
    assert len(container.hub.events_since(0)) == DEFAULT_BUFFER_SIZE
    assert container.hub.latest_event_id == DEFAULT_BUFFER_SIZE + 5


def test_slow_consumer_does_not_block_publisher() -> None:
    """慢消费者不影响发布：超出队列容量的事件被丢弃，发布立即返回。"""
    from app.infrastructure.realtime.hub import SUBSCRIBER_QUEUE_SIZE, RealtimeHub

    hub = RealtimeHub()
    with hub.subscribe() as subscriber:
        for index in range(SUBSCRIBER_QUEUE_SIZE + 10):
            hub.publish("tnt_demo", "test.fill", {"index": index})
        assert len(subscriber.queue) == SUBSCRIBER_QUEUE_SIZE
