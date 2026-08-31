"""内存仓储演示种子（M2）。

- 仅服务于 ``CONTROL_PLANE_REPOSITORY=memory`` 的本地演示与测试注入；
- **生产环境拒绝播种**（``APP_ENV=production`` 时抛错），避免把演示账号带进生产；
- 演示密码取自环境变量 ``CONTROL_PLANE_DEMO_PASSWORD``，未设置时用一次性随机密码
  并打印到启动日志：不把固定口令写进代码（SECURITY.md 密钥与凭证边界）。
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.domain.account import Account, AccountRole
from app.domain.catalog import FeatureGrant, ProductProfile, ProductProfileStatus
from app.domain.license import License, LicenseStatus
from app.domain.tenant import Tenant, TenantStatus
from app.infrastructure.auth.passwords import hash_password
from app.infrastructure.ids import (
    PREFIX_ACCOUNT,
    PREFIX_FEATURE_GRANT,
    PREFIX_LICENSE,
    PREFIX_TENANT,
    new_id,
)
from app.infrastructure.memory.repositories import MemoryRepositories

#: 演示商户与行业类型固定标识（测试断言依赖这些常量）
DEMO_TENANT_ID = "tnt_demo"
DEMO_PRODUCT_PROFILE_ID = "ppf_demo"

#: 演示账号登录名
DEMO_MERCHANT_LOGIN = "merchant_demo"
DEMO_DEVELOPER_LOGIN = "developer_demo"

#: 演示密码环境变量
DEMO_PASSWORD_ENV = "CONTROL_PLANE_DEMO_PASSWORD"

#: 演示许可证有效期（天）与设备数上限
DEMO_LICENSE_DAYS = 365
DEMO_MAX_DEVICES = 3

#: 演示商户开通的功能
DEMO_FEATURES: tuple[str, ...] = ("inventory-kpi", "abc-aging")


def build_memory_repositories(*, password: str | None = None) -> MemoryRepositories:
    """构建并播种内存仓储（本地演示 / 测试注入入口）。"""
    from app.settings import get_settings

    settings = get_settings()
    if settings.is_production:
        raise RuntimeError("生产环境禁止使用内存仓储（CONTROL_PLANE_REPOSITORY=memory）。")

    repos = MemoryRepositories()
    seed_demo(repos, password=password or resolve_demo_password())
    return repos


def resolve_demo_password() -> str:
    """解析演示密码：环境变量优先，缺失时生成一次性随机密码。"""
    configured = os.environ.get(DEMO_PASSWORD_ENV, "").strip()
    return configured or f"demo-{new_id('pwd')}"


def seed_demo(
    repos: MemoryRepositories,
    *,
    password: str,
    today: date | None = None,
    license_days: int = DEMO_LICENSE_DAYS,
) -> None:
    """写入一套可直接登录的演示数据：行业类型、商户、许可证、两个账号与功能授权。"""
    today = today or datetime.now(UTC).date()
    password_hash = hash_password(password)

    repos.entitlements.add_product_profile(
        ProductProfile(
            product_profile_id=DEMO_PRODUCT_PROFILE_ID,
            code="retail",
            name="零售",
            status=ProductProfileStatus.ACTIVE,
        )
    )
    repos.identity.add_tenant(
        Tenant(
            tenant_id=DEMO_TENANT_ID,
            name="演示商户",
            product_profile_id=DEMO_PRODUCT_PROFILE_ID,
            status=TenantStatus.ACTIVE,
        )
    )
    license_id = new_id(PREFIX_LICENSE)
    repos.entitlements.add_license(
        License(
            license_id=license_id,
            tenant_id=DEMO_TENANT_ID,
            product_profile_id=DEMO_PRODUCT_PROFILE_ID,
            starts_at=today,
            expires_at=today + timedelta(days=license_days),
            max_devices=DEMO_MAX_DEVICES,
            status=LicenseStatus.ACTIVE,
        )
    )
    for feature_code in DEMO_FEATURES:
        repos.entitlements.add_feature_grant(
            FeatureGrant(
                feature_grant_id=new_id(PREFIX_FEATURE_GRANT),
                tenant_id=DEMO_TENANT_ID,
                feature_code=feature_code,
            )
        )

    repos.identity.add_account(
        Account(
            account_id=new_id(PREFIX_ACCOUNT),
            login_name=DEMO_MERCHANT_LOGIN,
            password_hash=password_hash,
            role=AccountRole.MERCHANT_OWNER,
            tenant_id=DEMO_TENANT_ID,
        )
    )
    repos.identity.add_account(
        Account(
            account_id=new_id(PREFIX_ACCOUNT),
            login_name=DEMO_DEVELOPER_LOGIN,
            password_hash=password_hash,
            role=AccountRole.DEVELOPER,
            tenant_id=None,
        )
    )


def new_tenant(
    identity: Any,
    *,
    name: str,
    profile_id: str = DEMO_PRODUCT_PROFILE_ID,
) -> str:
    """追加一个商户（测试与演示用），返回 tenant_id。

    只依赖 identity 仓储的 ``add_tenant``，内存与 PostgreSQL 实现通用。
    """
    tenant_id = new_id(PREFIX_TENANT)
    identity.add_tenant(Tenant(tenant_id=tenant_id, name=name, product_profile_id=profile_id))
    return tenant_id
