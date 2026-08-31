"""许可证用例（M2）：状态评估、离线宽限期与访问放行判定。

宽限期规则（主基线 §10.3）：许可证到期后仍允许在宽限天数内继续工作，
超出宽限期的商户接口返回 ``LICENSE_EXPIRED``；宽限天数来自运行配置
``LICENSE_OFFLINE_GRACE_DAYS``（默认 7 天），不落许可证表，避免双写。

放行策略：``ACTIVE`` / ``GRACE`` 放行，``EXPIRED`` / ``REVOKED`` / ``MISSING`` 拒绝；
宽限期状态必须如实回传（UI 明示剩余天数），不静默放行。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.application.errors import license_expired
from app.application.ports import EntitlementRepository
from app.domain.catalog import ProductProfile
from app.domain.license import (
    DEFAULT_OFFLINE_GRACE_DAYS,
    EntitlementStatus,
    License,
    LicenseEntitlement,
    evaluate,
)


class EntitlementService:
    """许可证评估服务：把「许可证 + 行业类型 + 功能授权」折算成只读评估结果。"""

    def __init__(
        self,
        entitlements: EntitlementRepository,
        *,
        grace_days: int = DEFAULT_OFFLINE_GRACE_DAYS,
    ) -> None:
        self._entitlements = entitlements
        self._grace_days = grace_days

    @property
    def grace_days(self) -> int:
        """当前生效的离线宽限天数。"""
        return self._grace_days

    def evaluate(self, tenant_id: str | None, today: date | None = None) -> LicenseEntitlement:
        """评估商户许可证；无租户（开发者账号）返回 MISSING，不参与放行判定。"""
        if tenant_id is None:
            return evaluate(None, today or _today())
        license_ = self._entitlements.get_active_license(tenant_id)
        profile = (
            self._entitlements.get_product_profile(license_.product_profile_id)
            if license_ is not None
            else None
        )
        features = tuple(
            grant.feature_code for grant in self._entitlements.list_feature_grants(tenant_id)
        )
        return evaluate(
            license_,
            today or _today(),
            grace_days=self._grace_days,
            product_profile_code=profile.code if isinstance(profile, ProductProfile) else None,
            features=features,
        )

    def require_allowed(
        self,
        tenant_id: str | None,
        today: date | None = None,
    ) -> LicenseEntitlement:
        """评估并校验放行：拒绝时抛 ``LICENSE_EXPIRED``（403）。

        开发者账号（无 tenant）不走许可证判定。
        """
        if tenant_id is None:
            return evaluate(None, today or _today())
        entitlement = self.evaluate(tenant_id, today)
        if not entitlement.allowed:
            raise license_expired(
                _denied_message(entitlement),
                status=entitlement.status.value,
                reason=_denied_reason(entitlement),
                license_id=entitlement.license_id,
                expires_at=entitlement.expires_at.isoformat()
                if entitlement.expires_at
                else None,
                grace_ends_at=entitlement.grace_ends_at.isoformat()
                if entitlement.grace_ends_at
                else None,
            )
        return entitlement

    def active_license(self, tenant_id: str) -> License | None:
        """取商户当前 ACTIVE 许可证（开发者端与测试用）。"""
        return self._entitlements.get_active_license(tenant_id)


def _denied_message(entitlement: LicenseEntitlement) -> str:
    if entitlement.status is EntitlementStatus.REVOKED:
        return "许可证已被吊销，请联系服务商。"
    if entitlement.status is EntitlementStatus.MISSING:
        return "商户未开通有效许可证。"
    return "许可证已超出离线宽限期，请续期后继续使用。"


def _denied_reason(entitlement: LicenseEntitlement) -> str:
    return {
        EntitlementStatus.REVOKED: "LICENSE_REVOKED",
        EntitlementStatus.MISSING: "LICENSE_MISSING",
        EntitlementStatus.EXPIRED: "LICENSE_GRACE_EXCEEDED",
    }.get(entitlement.status, "LICENSE_NOT_ALLOWED")


def _today() -> date:
    """当前 UTC 日期（许可证到期判断按 UTC 自然日，主基线 §35.5）。"""
    return datetime.now(UTC).date()
