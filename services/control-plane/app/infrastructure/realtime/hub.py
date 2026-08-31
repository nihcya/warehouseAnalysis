"""状态流事件中心（M2，DECISIONS.md D-006：SSE + 30 秒轮询降级）。

职责：

- 为写操作（设备注册、许可证变更等）提供进程内事件发布入口；
- 维护**单调事件 ID** 与环形缓冲，支持 SSE 断线后的 ``Last-Event-ID`` 续传；
- 为每个 SSE 连接提供独立订阅队列（deque），按租户过滤事件。

线程模型：路由以同步函数运行在线程池，SSE 生成器运行在事件循环线程，
故订阅队列用 ``collections.deque``（append/popleft 线程安全），
发布侧用一把锁保护订阅者集合与序号，不使用 asyncio 原语跨线程操作。
"""

from __future__ import annotations

import itertools
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: 环形缓冲保留的最近事件数（SSE 断线重连补发窗口）
DEFAULT_BUFFER_SIZE = 200

#: 订阅者队列上限（超出即丢弃最旧事件，避免慢消费者拖垮内存）
SUBSCRIBER_QUEUE_SIZE = 100


@dataclass(eq=False)
class Subscriber:
    """一个 SSE 连接的订阅者：独立队列，按身份哈希（deque 本身不可哈希）。"""

    queue: deque = field(default_factory=lambda: deque(maxlen=SUBSCRIBER_QUEUE_SIZE))


@dataclass(frozen=True)
class StatusEvent:
    """一条状态流事件。"""

    event_id: int
    event_type: str
    payload: dict[str, Any]
    occurred_at: datetime
    tenant_id: str | None = None


def parse_last_event_id(value: str | None) -> int | None:
    """解析 Last-Event-ID（整数序号）；非法或非正值返回 None。"""
    if not value:
        return None
    try:
        event_id = int(value)
    except ValueError:
        return None
    return event_id if event_id > 0 else None


class RealtimeHub:
    """进程内状态流事件中心（发布 / 订阅 / 续传）。"""

    def __init__(self, buffer_size: int = DEFAULT_BUFFER_SIZE) -> None:
        self._buffer: deque[StatusEvent] = deque(maxlen=buffer_size)
        self._subscribers: set[Subscriber] = set()
        self._lock = threading.Lock()
        self._counter = itertools.count(1)

    @property
    def latest_event_id(self) -> int:
        """当前最新事件 ID（尚无事件时为 0）。"""
        with self._lock:
            return self._buffer[-1].event_id if self._buffer else 0

    def publish(self, tenant_id: str | None, event_type: str, payload: dict[str, Any]) -> str:
        """发布事件：写入环形缓冲并推送给匹配的订阅者，返回事件 ID 字符串。"""
        with self._lock:
            event = StatusEvent(
                event_id=next(self._counter),
                event_type=event_type,
                payload=payload,
                occurred_at=datetime.now(UTC),
                tenant_id=tenant_id,
            )
            self._buffer.append(event)
            subscribers = list(self._subscribers)

        for subscriber in subscribers:
            # 队列有 maxlen：慢消费者自动丢弃最旧事件，不阻塞发布方
            subscriber.queue.append(event)
        return str(event.event_id)

    def events_since(self, last_event_id: int | None) -> list[StatusEvent]:
        """返回晚于 ``last_event_id`` 的缓冲事件（续传补发）。"""
        with self._lock:
            if last_event_id is None:
                return []
            return [event for event in self._buffer if event.event_id > last_event_id]

    def drain(self, queue: deque[StatusEvent]) -> list[StatusEvent]:
        """取空一个订阅队列（SSE 生成器调用）。"""
        events: list[StatusEvent] = []
        while True:
            try:
                events.append(queue.popleft())
            except IndexError:
                return events

    @contextmanager
    def subscribe(self) -> Iterator[Subscriber]:
        """注册一个订阅者，退出上下文时自动注销（SSE 生成器用）。"""
        subscriber = Subscriber()
        with self._lock:
            self._subscribers.add(subscriber)
        try:
            yield subscriber
        finally:
            with self._lock:
                self._subscribers.discard(subscriber)
