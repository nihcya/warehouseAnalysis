"""SSE 状态流后台线程：订阅控制平面事件流，失败降级为轮询。

主基线 §10.1（降级可感知）：SSE 主通道，连接失败/断开 → 轮询降级 →
全部失败 → 离线等待重试 → 回到 SSE。通道状态经信号推送到 UI 主线程，
快照更新经 ``snapshot_received`` 信号分发。

SSE 帧格式（对齐控制平面 ``/api/v1/events/stream``）::

    event: snapshot
    data: {"event_id": "...", ...}

    : keepalive

空行（``\\n\\n``）为事件分界；以 ``:`` 开头的行是注释/保活，忽略。
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from PySide6.QtCore import QThread, Signal


class StatusStreamWorker(QThread):
    """后台线程：订阅 SSE 状态流，失败降级为轮询。

    通道优先级：SSE（实时）→ 轮询（降级）→ 离线（等待重试）→ 回到 SSE。
    信号自动跨线程传递到 UI 主线程（Qt 信号机制保证线程安全）。

    用法::

        worker = StatusStreamWorker(api_client)
        worker.snapshot_received.connect(handle_snapshot)
        worker.channel_changed.connect(update_channel_indicator)
        worker.start()
        # ...
        worker.stop()
        worker.wait()
    """

    #: 收到状态快照更新
    snapshot_received = Signal(dict)
    #: 通道变更（"connecting" / "sse" / "polling" / "offline"）
    channel_changed = Signal(str)
    #: 错误消息
    error_occurred = Signal(str)

    #: SSE 连接超时（秒）
    _SSE_CONNECT_TIMEOUT = 10.0
    #: SSE 读超时（秒）：超过此时间无数据视为连接断开
    _SSE_READ_TIMEOUT = 30.0
    #: 可中断睡眠的步长（秒）
    _SLEEP_STEP = 0.5

    def __init__(
        self,
        api_client: Any,
        poll_interval: int = 30,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """初始化状态流后台线程。

        :param api_client: HttpApiClient 实例（提供 stream_url / get_snapshot /
            access_token）。
        :param poll_interval: 轮询间隔（秒）。
        :param transport: 可选的 httpx 传输层（测试注入 MockTransport；
            生产留空则每次 SSE 连接新建独立 httpx.Client）。
        """
        super().__init__()
        self._api_client = api_client
        self._poll_interval = poll_interval
        self._transport = transport
        self._stop = False

    def stop(self) -> None:
        """请求线程退出（设置停止标志，run 循环将在下次检查时退出）。"""
        self._stop = True

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def run(self) -> None:
        """线程主循环：SSE → 轮询 → 离线，循环重试直到 stop()。"""
        while not self._stop:
            self.channel_changed.emit("connecting")
            # 1. 尝试 SSE 流式订阅（阻塞直到断开/失败）
            self._run_sse()
            if self._stop:
                break
            # 2. SSE 失败/断开 → 轮询降级
            self.channel_changed.emit("polling")
            self._run_polling()
            if self._stop:
                break
            # 3. 轮询也失败 → 离线，等待后重试 SSE
            self.channel_changed.emit("offline")
            self._sleep(self._poll_interval)

    # ------------------------------------------------------------------
    # SSE 通道
    # ------------------------------------------------------------------

    def _run_sse(self) -> None:
        """尝试 SSE 流式订阅，阻塞直到连接断开或 stop。

        连接成功时发 ``channel_changed("sse")``，收到快照发 ``snapshot_received``。
        连接失败或断开时直接返回（由主循环决定降级到轮询）。

        注意：SSE 使用独立的 httpx.Client（不复用 HttpApiClient 的同步客户端），
        因为流式请求会长时间阻塞底层连接。
        """
        token = getattr(self._api_client, "access_token", None)
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = self._api_client.stream_url()
        client_kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(
                connect=self._SSE_CONNECT_TIMEOUT,
                read=self._SSE_READ_TIMEOUT,
                write=self._SSE_CONNECT_TIMEOUT,
                pool=self._SSE_CONNECT_TIMEOUT,
            ),
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        try:
            with (
                httpx.Client(**client_kwargs) as client,
                client.stream("GET", url, headers=headers) as response,
            ):
                if response.status_code != 200:
                    self.error_occurred.emit(
                        f"SSE 连接失败：HTTP {response.status_code}"
                    )
                    return
                # 连接成功
                self.channel_changed.emit("sse")
                self._read_sse_stream(response)
        except httpx.HTTPError as exc:
            self.error_occurred.emit(f"SSE 连接异常：{exc}")

    def _read_sse_stream(self, response: httpx.Response) -> None:
        """逐行解析 SSE 帧，分发快照。

        帧格式：``event: snapshot\\ndata: {json}\\n\\n``；
        保活注释行（``:`` 开头）忽略。
        多行 ``data:`` 按 SSE 规范以 ``\\n`` 拼接后 JSON 解析。
        """
        event_type = ""
        data_lines: list[str] = []
        for line in response.iter_lines():
            if self._stop:
                return
            # iter_lines 可能保留行尾 \r（来自 \r\n），统一去除
            line = line.removesuffix("\r")
            if line == "":
                # 空行 = 事件分界，分发当前帧
                if data_lines:
                    self._dispatch_sse_event(event_type, data_lines)
                event_type = ""
                data_lines = []
                continue
            if line.startswith(":"):
                # 注释/保活行，忽略
                continue
            if line.startswith("event:"):
                event_type = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
            # 其他字段（id: / retry:）忽略
        # 流正常结束（服务端关闭），分发最后一个未分界的事件
        if data_lines:
            self._dispatch_sse_event(event_type, data_lines)

    def _dispatch_sse_event(
        self,
        event_type: str,
        data_lines: list[str],
    ) -> None:
        """分发单个 SSE 事件：拼接 data 行后 JSON 解析为快照。

        解析失败不中断连接，等待下一帧。
        """
        raw = "\n".join(data_lines)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(payload, dict):
            self.snapshot_received.emit(payload)

    # ------------------------------------------------------------------
    # 轮询通道
    # ------------------------------------------------------------------

    def _run_polling(self) -> None:
        """轮询降级：每 ``poll_interval`` 秒调用 get_snapshot，失败则返回。

        成功时发 ``snapshot_received``，失败（返回 None）时返回由主循环
        决定降级到离线。
        """
        while not self._stop:
            snapshot = self._api_client.get_snapshot()
            if self._stop:
                return
            if snapshot is None:
                # 轮询失败（api_client 离线）
                self.error_occurred.emit("轮询失败：控制平面不可达")
                return
            self.snapshot_received.emit(snapshot)
            self._sleep(self._poll_interval)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _sleep(self, seconds: float) -> None:
        """可被 stop 中断的等待（按步长轮询 _stop 标志）。"""
        remaining = seconds
        while remaining > 0 and not self._stop:
            step = min(self._SLEEP_STEP, remaining)
            time.sleep(step)
            remaining -= step
