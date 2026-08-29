"""账号（account）领域对象与状态机（M0 占位）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AccountStatus(str, Enum):
    """账号状态（M0 占位）。"""

    ACTIVE = "ACTIVE"  # 正常
    LOCKED = "LOCKED"  # 登录锁定（可解锁，如连续失败触发）
    DISABLED = "DISABLED"  # 停用（终态）


#: 允许的状态迁移表（源状态 -> 可达状态集合）
ACCOUNT_TRANSITIONS: dict[AccountStatus, frozenset[AccountStatus]] = {
    AccountStatus.ACTIVE: frozenset({AccountStatus.LOCKED, AccountStatus.DISABLED}),
    AccountStatus.LOCKED: frozenset({AccountStatus.ACTIVE, AccountStatus.DISABLED}),
    AccountStatus.DISABLED: frozenset(),
}


@dataclass
class Account:
    """账号实体（M0 占位）：仅承载标识与状态迁移校验，不接触 ORM。"""

    account_id: str
    tenant_id: str
    status: AccountStatus = AccountStatus.ACTIVE

    def transition(self, target: AccountStatus) -> None:
        """按状态机迁移；非法迁移抛 ValueError（M1+ 换领域异常并记错误码）。"""
        if target not in ACCOUNT_TRANSITIONS[self.status]:
            raise ValueError(f"非法状态迁移：{self.status.value} -> {target.value}")
        self.status = target
