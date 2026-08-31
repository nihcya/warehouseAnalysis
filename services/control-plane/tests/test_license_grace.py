"""许可证状态与离线宽限期测试（M2）。

域层直测（无 HTTP）+ API 放行/拒绝判定两层覆盖：
宽限期内放行并在响应里如实标注，超出宽限期返回 403 ``LICENSE_EXPIRED``。
默认宽限 7 天，可由 ``LICENSE_OFFLINE_GRACE_DAYS`` 覆盖（spec：7 天可配置）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from app.domain.license import (
    DEFAULT_OFFLINE_GRACE_DAYS,
    EntitlementStatus,
    License,
    LicenseStatus,
    evaluate,
    grace_end,
)
from app.domain.tenant import Tenant, TenantStatus
from conftest import DEMO_MERCHANT_LOGIN, TEST_PASSWORD, login_token
from fastapi.testclient import TestClient

TODAY = date(2026, 8, 30)


def make_license(
    *,
    starts: date = TODAY - timedelta(days=30),
    expires: date = TODAY + timedelta(days=30),
    status: LicenseStatus = LicenseStatus.ACTIVE,
    max_devices: int = 3,
) -> License:
    return License(
        license_id="lic_test",
        tenant_id="tnt_test",
        product_profile_id="ppf_demo",
        starts_at=starts,
        expires_at=expires,
        max_devices=max_devices,
        status=status,
    )


# ------------------------- 域层：评估矩阵 -------------------------


def test_default_grace_days_is_seven() -> None:
    """默认离线宽限期为 7 天（spec 确认值）。"""
    assert DEFAULT_OFFLINE_GRACE_DAYS == 7


def test_active_license_is_allowed() -> None:
    """有效期内：ACTIVE + allowed。"""
    entitlement = evaluate(make_license(), TODAY)
    assert entitlement.status is EntitlementStatus.ACTIVE
    assert entitlement.allowed
    assert not entitlement.in_grace
    assert entitlement.days_remaining == 30


def test_expired_within_grace_is_allowed() -> None:
    """过期 3 天（宽限 7 天内）：GRACE + 仍放行 + 给出宽限截止日。"""
    entitlement = evaluate(make_license(expires=TODAY - timedelta(days=3)), TODAY)
    assert entitlement.status is EntitlementStatus.GRACE
    assert entitlement.allowed
    assert entitlement.in_grace
    assert entitlement.grace_ends_at == TODAY + timedelta(days=4)
    assert entitlement.days_remaining == -3


def test_grace_boundary_last_day_is_allowed() -> None:
    """宽限期最后一天仍放行（到期日 + 宽限天数当日含在内）。"""
    license_ = make_license(expires=TODAY - timedelta(days=7))
    entitlement = evaluate(license_, TODAY)
    assert entitlement.status is EntitlementStatus.GRACE
    assert entitlement.allowed
    assert entitlement.grace_ends_at == grace_end(license_.expires_at, 7)


def test_expired_beyond_grace_is_denied() -> None:
    """过期第 8 天（超出宽限）：EXPIRED + 拒绝。"""
    entitlement = evaluate(make_license(expires=TODAY - timedelta(days=8)), TODAY)
    assert entitlement.status is EntitlementStatus.EXPIRED
    assert not entitlement.allowed


def test_grace_days_zero_denies_immediately() -> None:
    """宽限天数配置为 0：到期当日即拒绝。"""
    expired_yesterday = make_license(expires=TODAY - timedelta(days=1))
    assert evaluate(expired_yesterday, TODAY, grace_days=0).status is (
        EntitlementStatus.EXPIRED
    )
    # 到期当日仍有效（左闭：当天算在有效期内）
    expires_today = make_license(expires=TODAY)
    assert evaluate(expires_today, TODAY, grace_days=0).status is EntitlementStatus.ACTIVE


def test_revoked_license_is_denied() -> None:
    """已吊销：REVOKED + 拒绝（与到期日无关）。"""
    entitlement = evaluate(make_license(status=LicenseStatus.REVOKED), TODAY)
    assert entitlement.status is EntitlementStatus.REVOKED
    assert not entitlement.allowed


def test_missing_license_is_denied() -> None:
    """无许可证：MISSING + 拒绝（失败关闭，不放行）。"""
    entitlement = evaluate(None, TODAY)
    assert entitlement.status is EntitlementStatus.MISSING
    assert not entitlement.allowed


def test_negative_grace_days_rejected() -> None:
    """宽限天数为负数是配置错误：直接抛错，不静默当作 0。"""
    with pytest.raises(ValueError):
        evaluate(make_license(), TODAY, grace_days=-1)


def test_renewal_reminder_window() -> None:
    """续期提醒：30 天内到期或已进入宽限期时置位。"""
    soon = evaluate(make_license(expires=TODAY + timedelta(days=10)), TODAY)
    assert soon.needs_renewal_reminder
    later = evaluate(make_license(expires=TODAY + timedelta(days=90)), TODAY)
    assert not later.needs_renewal_reminder


# ------------------------- API：放行与拒绝 -------------------------


def _expire_demo_license(container: Any, days_ago: int) -> None:
    """把演示商户的许可证到期日改为 N 天前（内存仓储按引用保存）。"""
    license_ = container.entitlement_service.active_license("tnt_demo")
    assert license_ is not None
    license_.expires_at = datetime.now(UTC).date() - timedelta(days=days_ago)


def test_api_allows_requests_within_grace(client: TestClient, container: Any) -> None:
    """宽限期内：业务接口仍放行，响应如实标注 GRACE 与剩余天数。"""
    _expire_demo_license(container, days_ago=3)
    token = login_token(client, DEMO_MERCHANT_LOGIN)
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/v1/account/me", headers=headers)
    assert me.status_code == 200
    license_data = me.json()["data"]["license"]
    assert license_data["status"] == "GRACE"
    assert license_data["grace_days"] == 7
    assert license_data["days_remaining"] == -3
    assert license_data["grace_ends_at"] is not None

    assert client.get("/api/v1/devices", headers=headers).status_code == 200


def test_api_denies_requests_beyond_grace(client: TestClient, container: Any) -> None:
    """超出宽限期：业务接口 403 LICENSE_EXPIRED + reason=LICENSE_GRACE_EXCEEDED。"""
    _expire_demo_license(container, days_ago=10)
    token = login_token(client, DEMO_MERCHANT_LOGIN)
    headers = {"Authorization": f"Bearer {token}"}

    # 登录与 /account/me 不受阻，用户能看到"已过期"状态
    assert client.get("/api/v1/account/me", headers=headers).status_code == 200
    assert client.get("/api/v1/account/me", headers=headers).json()["data"]["license"][
        "status"
    ] == "EXPIRED"

    denied = client.get("/api/v1/devices", headers=headers)
    assert denied.status_code == 403
    error = denied.json()["error"]
    assert error["code"] == "LICENSE_EXPIRED"
    assert error["details"]["reason"] == "LICENSE_GRACE_EXCEEDED"

    register = client.post(
        "/api/v1/devices/register",
        json={"name": "过期后电脑", "fingerprint": "fp-expired"},
        headers=headers,
    )
    assert register.status_code == 403
    assert register.json()["error"]["code"] == "LICENSE_EXPIRED"


def test_suspended_tenant_cannot_login(client: TestClient, container: Any) -> None:
    """商户挂起：登录被拒（403 + reason=TENANT_SUSPENDED）。"""
    tenant = container.identity.get_tenant("tnt_demo")
    assert isinstance(tenant, Tenant)
    tenant.transition(TenantStatus.SUSPENDED)  # 内存仓储按引用保存，无需回写

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": DEMO_MERCHANT_LOGIN, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "AUTH_FORBIDDEN"
    assert error["details"]["reason"] == "TENANT_SUSPENDED"
