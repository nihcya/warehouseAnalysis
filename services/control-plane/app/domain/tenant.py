"""商户（tenant）领域对象与状态机（M2 扩展）。

M2 补充行业类型引用：行业差异通过 ``product_profile``、配置与许可证实现，
不为每个商户复制一套代码（主基线 §2.3、DECISIONS.md D-010）。
状态取值已由云端迁移 0002 冻结进 ``control_enum``（kind = tenant_status）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TenantStatus(str, Enum):
    """商户状态。"""

    ACTIVE = "ACTIVE"  # 正常服务
    SUSPENDED = "SUSPENDED"  # 暂停（欠费 / 违规 / 管理员操作）


#: 允许的状态迁移表（源状态 -> 可达状态集合）
TENANT_TRANSITIONS: dict[TenantStatus, frozenset[TenantStatus]] = {
    TenantStatus.ACTIVE: frozenset({TenantStatus.SUSPENDED}),
    TenantStatus.SUSPENDED: frozenset({TenantStatus.ACTIVE}),
}


@dataclass
class Tenant:
    """商户实体：标识、行业类型与状态迁移校验，不接触 ORM。"""

    tenant_id: str
    name: str
    product_profile_id: str | None = None
    status: TenantStatus = TenantStatus.ACTIVE

    def is_active(self) -> bool:
        """是否处于正常服务状态（挂起商户的接口访问由应用层拒绝）。"""
        return self.status is TenantStatus.ACTIVE

    def transition(self, target: TenantStatus) -> None:
        """按状态机迁移；非法迁移抛 ValueError。"""
        if target not in TENANT_TRANSITIONS[self.status]:
            raise ValueError(f"非法状态迁移：{self.status.value} -> {target.value}")
        self.status = target
