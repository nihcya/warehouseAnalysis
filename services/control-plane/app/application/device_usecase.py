"""设备注册用例（M2）。

规则：

1. 商户许可证必须处于放行状态（``ACTIVE`` / 宽限期内 ``GRACE``），否则拒绝；
2. 同一 ``(tenant_id, fingerprint)`` 只保留一台设备——重复注册按幂等处理：
   返回已有设备并刷新名称与客户端版本，不产生第二条记录；
3. 设备已吊销 → ``DEVICE_REVOKED``（重新绑定前不可恢复，主基线 §32.3）；
4. 在册（未吊销）设备数达到许可证 ``max_devices`` → 拒绝新设备
   （错误码沿用已冻结的 ``AUTH_FORBIDDEN``，details.reason = ``DEVICE_LIMIT_EXCEEDED``）；
5. 注册成功写审计并向状态流发布事件（SSE 与轮询降级共享）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.application.audit import AuditService
from app.application.errors import auth_forbidden, device_revoked
from app.application.license_usecase import EntitlementService
from app.application.ports import DeviceRepository, EventPublisher
from app.domain.audit import AuditAction, AuditResult
from app.domain.device import Device, DeviceStatus, DeviceType
from app.infrastructure.ids import PREFIX_DEVICE, new_id

#: 状态流事件类型：设备注册（Web 状态看板消费）
EVENT_DEVICE_REGISTERED = "device.registered"


class DeviceService:
    """设备注册服务。"""

    def __init__(
        self,
        devices: DeviceRepository,
        entitlements: EntitlementService,
        audit: AuditService,
        publisher: EventPublisher | None = None,
    ) -> None:
        self._devices = devices
        self._entitlements = entitlements
        self._audit = audit
        self._publisher = publisher

    def list_devices(self, tenant_id: str) -> list[Device]:
        """列出商户全部设备（含已吊销，UI 需明示吊销状态）。"""
        return self._devices.list_devices(tenant_id)

    def register(
        self,
        *,
        tenant_id: str,
        account_id: str,
        actor_role: str,
        device_type: DeviceType,
        name: str,
        fingerprint: str,
        app_version: str | None = None,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> Device:
        """注册设备；非法情形抛应用层错误。"""
        now = now or datetime.now(UTC)
        entitlement = self._entitlements.require_allowed(tenant_id)

        existing = self._devices.get_device_by_fingerprint(tenant_id, fingerprint)
        if existing is not None:
            if existing.is_revoked():
                self._audit.record(
                    action=AuditAction.DEVICE_REGISTER,
                    result=AuditResult.DENIED,
                    occurred_at=now,
                    actor_account_id=account_id,
                    actor_role=actor_role,
                    tenant_id=tenant_id,
                    target_type="device",
                    target_id=existing.device_id,
                    request_id=request_id,
                    detail={"reason": "DEVICE_REVOKED"},
                )
                raise device_revoked(device_id=existing.device_id)
            # 幂等：同一台机器重复注册，只刷新名称与版本
            existing.rename(name, app_version, now)
            self._devices.save_device(existing)
            self._audit.record(
                action=AuditAction.DEVICE_REGISTER,
                result=AuditResult.SUCCESS,
                occurred_at=now,
                actor_account_id=account_id,
                actor_role=actor_role,
                tenant_id=tenant_id,
                target_type="device",
                target_id=existing.device_id,
                request_id=request_id,
                detail={"reason": "ALREADY_REGISTERED", "device_type": device_type.value},
            )
            return existing

        registered = self._devices.count_registered_devices(tenant_id)
        if registered >= entitlement.max_devices:
            self._audit.record(
                action=AuditAction.DEVICE_REGISTER,
                result=AuditResult.DENIED,
                occurred_at=now,
                actor_account_id=account_id,
                actor_role=actor_role,
                tenant_id=tenant_id,
                request_id=request_id,
                detail={"reason": "DEVICE_LIMIT_EXCEEDED"},
            )
            raise auth_forbidden(
                "已注册设备数达到许可证上限。",
                reason="DEVICE_LIMIT_EXCEEDED",
                max_devices=entitlement.max_devices,
                registered_devices=registered,
            )

        device = Device(
            device_id=new_id(PREFIX_DEVICE),
            tenant_id=tenant_id,
            device_type=device_type,
            name=name,
            fingerprint=fingerprint,
            status=DeviceStatus.REGISTERED,
            app_version=app_version,
            registered_at=now,
            updated_at=now,
        )
        self._devices.add_device(device)
        self._audit.record(
            action=AuditAction.DEVICE_REGISTER,
            result=AuditResult.SUCCESS,
            occurred_at=now,
            actor_account_id=account_id,
            actor_role=actor_role,
            tenant_id=tenant_id,
            target_type="device",
            target_id=device.device_id,
            request_id=request_id,
            detail={"device_type": device_type.value},
        )
        if self._publisher is not None:
            self._publisher.publish(
                tenant_id,
                EVENT_DEVICE_REGISTERED,
                {
                    "device_id": device.device_id,
                    "name": device.name,
                    "device_type": device.device_type.value,
                    "status": device.status.value,
                    "app_version": device.app_version,
                    "registered_at": device.registered_at.isoformat()
                    if device.registered_at
                    else None,
                },
            )
        return device
