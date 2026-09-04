"""设备心跳用例（M3 实装）。

规则：

1. 设备必须已注册且归属当前租户（租户隔离，不信任前端传入的 tenant_id）；
2. 心跳按 ``device_id`` upsert 最新投影（一行一台设备，只保留最新）；
3. 顺带刷新 ``device.last_seen_at`` 并把设备迁移为 ONLINE
   （REGISTERED / OFFLINE / DEGRADED → ONLINE，按设备状态机合法迁移），
   Web 端设备列表据此显示在线；
4. 发布 ``device.heartbeat`` 状态流事件（SSE 与轮询降级共享）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.application.errors import device_revoked, validation_failed
from app.application.ports import DeviceRepository, EventPublisher, HeartbeatRepository
from app.domain.device import DeviceStatus
from app.domain.heartbeat import Heartbeat

#: 状态流事件类型：设备心跳（Web 状态看板消费）
EVENT_DEVICE_HEARTBEAT = "device.heartbeat"

#: 可迁移为 ONLINE 的源状态（ONLINE 本身无需迁移，非法自迁移会被状态机拒绝）
_ONLINE_SOURCE_STATES = (DeviceStatus.REGISTERED, DeviceStatus.OFFLINE, DeviceStatus.DEGRADED)


class HeartbeatService:
    """设备心跳应用服务。"""

    def __init__(
        self,
        heartbeats: HeartbeatRepository,
        devices: DeviceRepository,
        publisher: EventPublisher | None = None,
    ) -> None:
        self._heartbeats = heartbeats
        self._devices = devices
        self._publisher = publisher

    def report(
        self,
        *,
        tenant_id: str,
        device_id: str,
        status: str,
        app_version: str | None = None,
        engine_version: str | None = None,
        db_schema_version: str | None = None,
        pending_sync_count: int = 0,
        now: datetime | None = None,
    ) -> Heartbeat:
        """上报心跳并 upsert 最新投影；设备不存在或不属于本租户拒绝。"""
        now = now or datetime.now(UTC)
        device = self._devices.get_device(device_id)
        if device is None or device.tenant_id != tenant_id:
            raise validation_failed(
                "设备不存在或不属于当前商户。",
                reason="DEVICE_NOT_FOUND",
                device_id=device_id,
            )
        if device.is_revoked():
            raise device_revoked(device_id=device_id)

        stored = self._heartbeats.upsert(
            Heartbeat(
                device_id=device_id,
                tenant_id=tenant_id,
                sent_at=now,
                status=status,
                app_version=app_version,
                engine_version=engine_version,
                db_schema_version=db_schema_version,
                pending_sync_count=pending_sync_count,
            )
        )

        # 刷新设备在线投影：last_seen_at 永远更新；状态仅在合法迁移时转 ONLINE
        device.last_seen_at = now
        if device.status in _ONLINE_SOURCE_STATES:
            device.transition(DeviceStatus.ONLINE)
        self._devices.save_device(device)

        if self._publisher is not None:
            self._publisher.publish(
                tenant_id,
                EVENT_DEVICE_HEARTBEAT,
                {
                    "device_id": device_id,
                    "status": status,
                    "pending_sync_count": pending_sync_count,
                    "app_version": app_version,
                    "sent_at": stored.sent_at.isoformat(),
                },
            )
        return stored
