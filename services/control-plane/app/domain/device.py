"""设备（device）领域对象与状态机（M0 占位）。

状态取值对齐主基线 §32.3：
``REGISTERED -> ONLINE -> DEGRADED -> OFFLINE``，``REVOKED`` 为终态
（设备吊销后不能刷新授权，重新绑定前不恢复）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DeviceStatus(str, Enum):
    """设备状态（主基线 §32.3）。"""

    REGISTERED = "REGISTERED"  # 已注册（尚未上线）
    ONLINE = "ONLINE"  # 最近心跳在阈值内且配置已确认
    DEGRADED = "DEGRADED"  # 心跳正常但配置过期 / 同步积压 / 最近任务失败
    OFFLINE = "OFFLINE"  # 超过心跳阈值没有上报
    REVOKED = "REVOKED"  # 已吊销（终态）


#: 允许的状态迁移表（源状态 -> 可达状态集合）
DEVICE_TRANSITIONS: dict[DeviceStatus, frozenset[DeviceStatus]] = {
    DeviceStatus.REGISTERED: frozenset({DeviceStatus.ONLINE, DeviceStatus.REVOKED}),
    DeviceStatus.ONLINE: frozenset({DeviceStatus.DEGRADED, DeviceStatus.OFFLINE}),
    DeviceStatus.DEGRADED: frozenset({DeviceStatus.ONLINE, DeviceStatus.OFFLINE, DeviceStatus.REVOKED}),
    DeviceStatus.OFFLINE: frozenset({DeviceStatus.ONLINE, DeviceStatus.DEGRADED, DeviceStatus.REVOKED}),
    DeviceStatus.REVOKED: frozenset(),
}


@dataclass
class Device:
    """设备实体（M0 占位）：仅承载标识与状态迁移校验，不接触 ORM。"""

    device_id: str
    tenant_id: str
    status: DeviceStatus = DeviceStatus.REGISTERED

    def transition(self, target: DeviceStatus) -> None:
        """按状态机迁移；非法迁移抛 ValueError（M1+ 换领域异常并记错误码）。"""
        if target not in DEVICE_TRANSITIONS[self.status]:
            raise ValueError(f"非法状态迁移：{self.status.value} -> {target.value}")
        self.status = target
