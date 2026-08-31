"""账号（account）领域对象与状态机（M2 实装）。

- 状态机：``ACTIVE <-> LOCKED -> DISABLED``，``DISABLED`` 为终态；
- 角色：``MERCHANT_OWNER``（商户主账号，必须绑定租户）与 ``DEVELOPER``（开发者账号，
  不绑定租户，主基线 §35.6）；
- 连续登录失败达阈值即临时锁定，锁定到期自动恢复可用（不写状态，按时间判断）。

本模块只承载业务规则，不接触 ORM、FastAPI 与密码学实现：
密码哈希由 ``app.infrastructure.auth.passwords`` 负责，账号只保存哈希字符串。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

#: 连续登录失败触发临时锁定的次数阈值
MAX_FAILED_ATTEMPTS = 5

#: 锁定持续时长（自最后一次失败起算）
LOCK_DURATION = timedelta(minutes=15)


class AccountStatus(str, Enum):
    """账号状态。"""

    ACTIVE = "ACTIVE"  # 正常
    LOCKED = "LOCKED"  # 登录锁定（连续失败触发，到期自动恢复）
    DISABLED = "DISABLED"  # 停用（终态）


#: 允许的状态迁移表（源状态 -> 可达状态集合）
ACCOUNT_TRANSITIONS: dict[AccountStatus, frozenset[AccountStatus]] = {
    AccountStatus.ACTIVE: frozenset({AccountStatus.LOCKED, AccountStatus.DISABLED}),
    AccountStatus.LOCKED: frozenset({AccountStatus.ACTIVE, AccountStatus.DISABLED}),
    AccountStatus.DISABLED: frozenset(),
}


class AccountRole(str, Enum):
    """账号角色（决定 Scope，主基线 §35.6）。"""

    MERCHANT_OWNER = "MERCHANT_OWNER"  # 商户主账号：merchant scope
    DEVELOPER = "DEVELOPER"  # 开发者账号：developer scope


#: 角色 -> Scope 集合（api/v1/deps.py 的鉴权依赖按此校验）
ROLE_SCOPES: dict[AccountRole, frozenset[str]] = {
    AccountRole.MERCHANT_OWNER: frozenset({"merchant"}),
    AccountRole.DEVELOPER: frozenset({"developer"}),
}


@dataclass
class Account:
    """账号实体：标识、凭证哈希、角色与登录失败治理。

    ``tenant_id`` 对开发者账号为 ``None``；商户账号必须绑定租户
    （数据库层由 ``ck_account_tenant_required_for_merchant`` 强制）。
    """

    account_id: str
    login_name: str
    password_hash: str
    role: AccountRole
    tenant_id: str | None = None
    status: AccountStatus = AccountStatus.ACTIVE
    failed_attempts: int = 0
    locked_until: datetime | None = None
    last_login_at: datetime | None = None

    def scopes(self) -> frozenset[str]:
        """返回该角色拥有的 Scope 集合。"""
        return ROLE_SCOPES[self.role]

    def is_locked(self, now: datetime) -> bool:
        """是否处于锁定期：状态为 LOCKED 且锁定未到期（到期视为已恢复）。"""
        if self.status is not AccountStatus.LOCKED:
            return False
        return self.locked_until is not None and now < self.locked_until

    def can_login(self, now: datetime) -> bool:
        """是否允许尝试登录：状态正常或锁定已到期。"""
        if self.status is AccountStatus.DISABLED:
            return False
        return not self.is_locked(now)

    def register_failure(self, now: datetime) -> None:
        """记录一次登录失败；达到阈值时转入 LOCKED 并设置到期时间。"""
        if self.status is AccountStatus.DISABLED:
            return
        self.failed_attempts += 1
        if self.failed_attempts >= MAX_FAILED_ATTEMPTS:
            self.status = AccountStatus.LOCKED
            self.locked_until = now + LOCK_DURATION

    def register_success(self, now: datetime) -> None:
        """记录一次登录成功：清零失败计数、解除锁定并刷新最后登录时间。"""
        self.failed_attempts = 0
        self.locked_until = None
        if self.status is AccountStatus.LOCKED:
            self.status = AccountStatus.ACTIVE
        self.last_login_at = now

    def transition(self, target: AccountStatus) -> None:
        """按状态机迁移；非法迁移抛 ValueError。"""
        if target not in ACCOUNT_TRANSITIONS[self.status]:
            raise ValueError(f"非法状态迁移：{self.status.value} -> {target.value}")
        self.status = target
