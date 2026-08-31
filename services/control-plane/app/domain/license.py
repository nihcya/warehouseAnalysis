"""许可证（license）领域对象、状态机与离线宽限期评估（M2 实装）。

离线宽限期（主基线 §10.3）：
许可证到期后仍允许工作台在离线/断网状态下继续使用本地功能一段时间，
宽限天数可配置（默认 7 天）；超出宽限期后商户接口返回 ``LICENSE_EXPIRED``，
进入只读或限制模式。宽限天数属运行配置，不落许可证表，避免双写。

评估结果 ``LicenseEntitlement`` 是只读快照：
``ACTIVE`` / ``GRACE`` 放行，``EXPIRED`` / ``REVOKED`` / ``MISSING`` 拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum


class LicenseStatus(str, Enum):
    """许可证状态（同一商户只能有一个 ACTIVE）。"""

    ACTIVE = "ACTIVE"  # 时间范围内有效
    EXPIRED = "EXPIRED"  # 已过期（续期后恢复 ACTIVE）
    REVOKED = "REVOKED"  # 已吊销（终态）


#: 允许的状态迁移表（源状态 -> 可达状态集合）
LICENSE_TRANSITIONS: dict[LicenseStatus, frozenset[LicenseStatus]] = {
    LicenseStatus.ACTIVE: frozenset({LicenseStatus.EXPIRED, LicenseStatus.REVOKED}),
    LicenseStatus.EXPIRED: frozenset({LicenseStatus.ACTIVE, LicenseStatus.REVOKED}),
    LicenseStatus.REVOKED: frozenset(),
}


class EntitlementStatus(str, Enum):
    """许可证评估结果（面向 API 与 UI 的只读状态）。"""

    ACTIVE = "ACTIVE"  # 有效期内
    GRACE = "GRACE"  # 已过期但在离线宽限期内，本地功能可用
    EXPIRED = "EXPIRED"  # 已过期且超出宽限期
    REVOKED = "REVOKED"  # 已吊销
    MISSING = "MISSING"  # 商户无有效许可证（未开通或已全部失效）


#: 拒绝访问的评估结果（api 层据此抛 403 LICENSE_EXPIRED）
DENIED_STATUSES: frozenset[EntitlementStatus] = frozenset(
    {
        EntitlementStatus.EXPIRED,
        EntitlementStatus.REVOKED,
        EntitlementStatus.MISSING,
    }
)

#: 默认离线宽限天数（可由 LICENSE_OFFLINE_GRACE_DAYS 覆盖）
DEFAULT_OFFLINE_GRACE_DAYS = 7

#: 提前提醒续期的天数阈值（UI 展示用，不阻断）
RENEWAL_REMINDER_DAYS = 30


@dataclass(frozen=True)
class LicenseEntitlement:
    """许可证评估结果快照：状态、到期与宽限期、设备上限与功能授权。"""

    status: EntitlementStatus
    license_id: str | None = None
    product_profile_code: str | None = None
    starts_at: date | None = None
    expires_at: date | None = None
    grace_days: int = DEFAULT_OFFLINE_GRACE_DAYS
    grace_ends_at: date | None = None
    days_remaining: int | None = None  # 到期剩余天数（负数表示已过期天数）
    max_devices: int = 0
    features: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        """是否放行云端接口访问（宽限期内仍放行，保证本地功能可用）。"""
        return self.status not in DENIED_STATUSES

    @property
    def in_grace(self) -> bool:
        """是否处于离线宽限期（UI 必须明示剩余天数，主基线 §10.3）。"""
        return self.status is EntitlementStatus.GRACE

    @property
    def needs_renewal_reminder(self) -> bool:
        """是否进入续期提醒窗口（30 天内到期或已在宽限期）。"""
        if self.status is EntitlementStatus.GRACE:
            return True
        return self.days_remaining is not None and self.days_remaining <= RENEWAL_REMINDER_DAYS


@dataclass
class License:
    """许可证实体：归属商户、行业类型、有效期与设备数上限。"""

    license_id: str
    tenant_id: str
    product_profile_id: str
    starts_at: date
    expires_at: date
    max_devices: int = 1
    status: LicenseStatus = LicenseStatus.ACTIVE

    def expired(self, today: date) -> bool:
        """按到期日判断是否过期（过期不自动改状态，由迁移/运维驱动）。"""
        return today > self.expires_at

    def transition(self, target: LicenseStatus) -> None:
        """按状态机迁移；非法迁移抛 ValueError。"""
        if target not in LICENSE_TRANSITIONS[self.status]:
            raise ValueError(f"非法状态迁移：{self.status.value} -> {target.value}")
        self.status = target


def grace_end(expires_at: date, grace_days: int) -> date:
    """宽限期截止日（含当天）：到期日 + 宽限天数。"""
    return expires_at + timedelta(days=grace_days)


def evaluate(
    license_: License | None,
    today: date,
    *,
    grace_days: int = DEFAULT_OFFLINE_GRACE_DAYS,
    product_profile_code: str | None = None,
    features: tuple[str, ...] = (),
) -> LicenseEntitlement:
    """评估商户许可证，返回只读评估结果。

    判定顺序：无许可证 / 已吊销 / 已过期（分是否落在宽限期）/ 有效期内。
    宽限天数取正值；传入 0 表示不设宽限（到期即拒绝）。
    """
    if grace_days < 0:
        raise ValueError("grace_days 不能为负数")

    if license_ is None:
        return LicenseEntitlement(status=EntitlementStatus.MISSING, grace_days=grace_days)

    if license_.status is LicenseStatus.REVOKED:
        return LicenseEntitlement(
            status=EntitlementStatus.REVOKED,
            license_id=license_.license_id,
            product_profile_code=product_profile_code,
            starts_at=license_.starts_at,
            expires_at=license_.expires_at,
            grace_days=grace_days,
            days_remaining=(license_.expires_at - today).days,
            max_devices=license_.max_devices,
            features=features,
        )

    days_remaining = (license_.expires_at - today).days
    if days_remaining < 0:
        ends_at = grace_end(license_.expires_at, grace_days)
        in_grace = today <= ends_at
        return LicenseEntitlement(
            status=EntitlementStatus.GRACE if in_grace else EntitlementStatus.EXPIRED,
            license_id=license_.license_id,
            product_profile_code=product_profile_code,
            starts_at=license_.starts_at,
            expires_at=license_.expires_at,
            grace_days=grace_days,
            grace_ends_at=ends_at,
            days_remaining=days_remaining,
            max_devices=license_.max_devices,
            features=features,
        )

    return LicenseEntitlement(
        status=EntitlementStatus.ACTIVE,
        license_id=license_.license_id,
        product_profile_code=product_profile_code,
        starts_at=license_.starts_at,
        expires_at=license_.expires_at,
        grace_days=grace_days,
        grace_ends_at=grace_end(license_.expires_at, grace_days),
        days_remaining=days_remaining,
        max_devices=license_.max_devices,
        features=features,
    )
