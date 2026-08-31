"""设备（device）领域对象与状态机（M2 实装）。

状态取值对齐主基线 §32.3：
``REGISTERED -> ONLINE -> DEGRADED -> OFFLINE``，``REVOKED`` 为终态
（设备吊销后不能刷新授权，重新绑定前不恢复）。

M2 只落地注册与吊销语义：心跳、在线判定与降级属 M3（托盘 Agent），
因此 ``ONLINE`` / ``DEGRADED`` / ``OFFLINE`` 在本阶段只作为状态取值存在，
由 M3 的状态上报驱动。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
    DeviceStatus.DEGRADED: frozenset(
        {DeviceStatus.ONLINE, DeviceStatus.OFFLINE, DeviceStatus.REVOKED}
    ),
    DeviceStatus.OFFLINE: frozenset(
        {DeviceStatus.ONLINE, DeviceStatus.DEGRADED, DeviceStatus.REVOKED}
    ),
    DeviceStatus.REVOKED: frozenset(),
}


class DeviceType(str, Enum):
    """设备类型（主基线 §7.2：工作台、Web 会话和小程序设备）。"""

    DESKTOP = "DESKTOP"
    WEB = "WEB"
    MINI_PROGRAM = "MINI_PROGRAM"


@dataclass
class Device:
    """设备实体：归属租户、类型、指纹与状态。

    ``(tenant_id, fingerprint)`` 全局唯一（数据库层强制），
    同一台机器重复注册按幂等处理，不产生第二条记录。
    """

    device_id: str
    tenant_id: str
    device_type: DeviceType
    name: str
    fingerprint: str
    status: DeviceStatus = DeviceStatus.REGISTERED
    app_version: str | None = None
    last_seen_at: datetime | None = None
    registered_at: datetime | None = None
    updated_at: datetime | None = None

    def is_revoked(self) -> bool:
        """是否已吊销（吊销后不得复用，需开发者重新绑定）。"""
        return self.status is DeviceStatus.REVOKED

    def rename(self, name: str, app_version: str | None, now: datetime) -> None:
        """更新重复注册带回的名称与客户端版本（幂等注册路径使用）。"""
        self.name = name
        self.app_version = app_version
        self.updated_at = now

    def transition(self, target: DeviceStatus) -> None:
        """按状态机迁移；非法迁移抛 ValueError。"""
        if target not in DEVICE_TRANSITIONS[self.status]:
            raise ValueError(f"非法状态迁移：{self.status.value} -> {target.value}")
        self.status = target
