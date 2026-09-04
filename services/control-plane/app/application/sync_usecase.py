"""小程序事件同步链路用例（M3 实装，云端中继侧）。

- ``inject``（dev 工具）：Mock 小程序事件 → Fernet 加密 → 密文信封写入
  ``sync_envelope``（event_id 全局唯一，重复注入 409 DUPLICATE_EVENT）；
- ``pull``：按设备拉取 ENQUEUED 加密信封，拉取时顺带清理 TTL 过期信封；
  客户端断网重试期间信封保持 ENQUEUED（spec：不伪造成功）；
- ``ack``：应用成功后的幂等确认——重复 ACK 幂等成功（already_acked=True），
  不存在或跨租户的信封拒绝（不泄露存在性）。

生产环境禁用注入端点（由路由层守卫）；本服务不校验运行环境。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.errors import device_revoked, duplicate_event, validation_failed
from app.application.ports import DeviceRepository, SyncEnvelopeRepository
from app.domain.sync import SyncEnvelope, SyncEnvelopeStatus
from app.infrastructure.ids import PREFIX_SYNC_ENVELOPE, PREFIX_SYNC_EVENT, new_id
from app.infrastructure.sync_crypto import encrypt_json

#: 默认信封 TTL（秒）：7 天，过期后拉取时自动清理
DEFAULT_ENVELOPE_TTL_SECONDS = 7 * 24 * 60 * 60

#: 设备单次拉取信封的默认上限
DEFAULT_PULL_LIMIT = 50


class SyncService:
    """同步信封应用服务。"""

    def __init__(
        self,
        envelopes: SyncEnvelopeRepository,
        devices: DeviceRepository,
        encryption_key: str,
    ) -> None:
        self._envelopes = envelopes
        self._devices = devices
        self._encryption_key = encryption_key

    def pull(
        self,
        *,
        tenant_id: str,
        device_id: str,
        limit: int = DEFAULT_PULL_LIMIT,
        now: datetime | None = None,
    ) -> list[SyncEnvelope]:
        """按设备拉取 ENQUEUED 信封；拉取时顺带清理 TTL 过期信封。"""
        now = now or datetime.now(UTC)
        self._require_device(tenant_id, device_id)
        self._envelopes.delete_expired(now)
        return self._envelopes.list_enqueued(
            tenant_id=tenant_id, target_device_id=device_id, limit=limit, now=now
        )

    def ack(
        self, *, tenant_id: str, envelope_id: str, now: datetime | None = None
    ) -> tuple[SyncEnvelope, bool]:
        """确认信封已应用；返回 (信封, 是否重复确认)。

        幂等语义：已 ACKED 的信封重复确认直接成功（already_acked=True），
        不存在的信封按校验失败拒绝（客户端重试窗口内信封不会消失，
        只有 TTL 过期清理才会移除）。
        """
        now = now or datetime.now(UTC)
        envelope = self._envelopes.get(envelope_id)
        if envelope is None or envelope.tenant_id != tenant_id:
            raise validation_failed(
                "同步信封不存在。",
                reason="SYNC_ENVELOPE_NOT_FOUND",
                envelope_id=envelope_id,
            )
        if envelope.status is SyncEnvelopeStatus.ACKED:
            return envelope, True
        marked = self._envelopes.mark_acked(envelope_id, now)
        if marked is None:
            # 并发兜底：另一请求刚把信封置为 ACKED
            current = self._envelopes.get(envelope_id)
            if current is None:
                raise validation_failed(
                    "同步信封不存在。",
                    reason="SYNC_ENVELOPE_NOT_FOUND",
                    envelope_id=envelope_id,
                )
            return current, True
        return marked, False

    def inject(
        self,
        *,
        tenant_id: str,
        target_device_id: str,
        payload: dict[str, Any],
        event_id: str | None = None,
        idempotency_key: str | None = None,
        ttl_seconds: int = DEFAULT_ENVELOPE_TTL_SECONDS,
        now: datetime | None = None,
    ) -> SyncEnvelope:
        """dev 工具：Mock 小程序事件加密为信封写入（生产环境由路由层禁用）。"""
        now = now or datetime.now(UTC)
        self._require_device(tenant_id, target_device_id)
        event_id = event_id or new_id(PREFIX_SYNC_EVENT)
        if self._envelopes.get_by_event_id(event_id) is not None:
            raise duplicate_event("event_id 已存在。", event_id=event_id)
        envelope = SyncEnvelope(
            envelope_id=new_id(PREFIX_SYNC_ENVELOPE),
            tenant_id=tenant_id,
            target_device_id=target_device_id,
            event_id=event_id,
            ciphertext=encrypt_json(payload, self._encryption_key),
            idempotency_key=idempotency_key,
            status=SyncEnvelopeStatus.ENQUEUED,
            expires_at=now + timedelta(seconds=ttl_seconds),
            created_at=now,
        )
        self._envelopes.add(envelope)
        return envelope

    def _require_device(self, tenant_id: str, device_id: str) -> None:
        """校验目标设备已注册、归属本租户且未吊销。"""
        device = self._devices.get_device(device_id)
        if device is None or device.tenant_id != tenant_id:
            raise validation_failed(
                "设备不存在或不属于当前商户。",
                reason="DEVICE_NOT_FOUND",
                device_id=device_id,
            )
        if device.is_revoked():
            raise device_revoked(device_id=device_id)
