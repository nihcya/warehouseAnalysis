"""许可证（license）领域对象与状态机（M0 占位）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class LicenseStatus(str, Enum):
    """许可证状态（M0 占位；同一商户只能有一个 ACTIVE）。"""

    ACTIVE = "ACTIVE"  # 时间范围内有效
    EXPIRED = "EXPIRED"  # 已过期（续期后恢复 ACTIVE）
    REVOKED = "REVOKED"  # 已吊销（终态）


#: 允许的状态迁移表（源状态 -> 可达状态集合）
LICENSE_TRANSITIONS: dict[LicenseStatus, frozenset[LicenseStatus]] = {
    LicenseStatus.ACTIVE: frozenset({LicenseStatus.EXPIRED, LicenseStatus.REVOKED}),
    LicenseStatus.EXPIRED: frozenset({LicenseStatus.ACTIVE, LicenseStatus.REVOKED}),
    LicenseStatus.REVOKED: frozenset(),
}


@dataclass
class License:
    """许可证实体（M0 占位）：仅承载标识与状态迁移校验，不接触 ORM。"""

    license_id: str
    tenant_id: str
    status: LicenseStatus = LicenseStatus.ACTIVE
    expires_at: date | None = None

    def expired(self, today: date) -> bool:
        """按到期日判断是否过期（M0 占位：过期不自动改状态，由迁移驱动）。"""
        return self.expires_at is not None and today > self.expires_at

    def transition(self, target: LicenseStatus) -> None:
        """按状态机迁移；非法迁移抛 ValueError（M1+ 换领域异常并记错误码）。"""
        if target not in LICENSE_TRANSITIONS[self.status]:
            raise ValueError(f"非法状态迁移：{self.status.value} -> {target.value}")
        self.status = target
