"""商户（tenant）领域对象与状态机（M0 占位）。

M1+ 引入持久化与领域事件后扩展；状态取值届时再冻结进 control_enum。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TenantStatus(str, Enum):
    """商户状态（M0 占位）。"""

    ACTIVE = "ACTIVE"  # 正常服务
    SUSPENDED = "SUSPENDED"  # 暂停（欠费 / 违规 / 管理员操作）


#: 允许的状态迁移表（源状态 -> 可达状态集合）
TENANT_TRANSITIONS: dict[TenantStatus, frozenset[TenantStatus]] = {
    TenantStatus.ACTIVE: frozenset({TenantStatus.SUSPENDED}),
    TenantStatus.SUSPENDED: frozenset({TenantStatus.ACTIVE}),
}


@dataclass
class Tenant:
    """商户实体（M0 占位）：仅承载标识与状态迁移校验，不接触 ORM。"""

    tenant_id: str
    name: str
    status: TenantStatus = TenantStatus.ACTIVE

    def transition(self, target: TenantStatus) -> None:
        """按状态机迁移；非法迁移抛 ValueError（M1+ 换领域异常并记错误码）。"""
        if target not in TENANT_TRANSITIONS[self.status]:
            raise ValueError(f"非法状态迁移：{self.status.value} -> {target.value}")
        self.status = target
