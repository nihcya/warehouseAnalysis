"""内存仓储演示种子（M2 + M3）。

- 仅服务于 ``CONTROL_PLANE_REPOSITORY=memory`` 的本地演示与测试注入；
- **生产环境拒绝播种**（``APP_ENV=production`` 时抛错），避免把演示账号带进生产；
- 演示密码取自环境变量 ``CONTROL_PLANE_DEMO_PASSWORD``，未设置时用一次性随机密码
  并打印到启动日志：不把固定口令写进代码（SECURITY.md 密钥与凭证边界）。

M3 追加播种：一份 PUBLISHED 演示配置（带 SHA-256 摘要与签名，供验签联调）、
两个演示调度任务与一个待拉取（CREATED）的运行投影。
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from app.application.config_usecase import ConfigService
from app.domain.account import Account, AccountRole
from app.domain.catalog import FeatureGrant, ProductProfile, ProductProfileStatus
from app.domain.license import License, LicenseStatus
from app.domain.task import RunStatus, Task, TaskRun
from app.domain.tenant import Tenant, TenantStatus
from app.infrastructure.auth.passwords import hash_password
from app.infrastructure.ids import (
    PREFIX_ACCOUNT,
    PREFIX_FEATURE_GRANT,
    PREFIX_LICENSE,
    PREFIX_TASK,
    PREFIX_TASK_RUN,
    PREFIX_TENANT,
    new_id,
)
from app.infrastructure.memory.repositories import MemoryRepositories

if TYPE_CHECKING:
    from app.settings import Settings

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

#: 演示配置版本与内容（M3：GET /config 验签联调用）
DEMO_CONFIG_VERSION = "2026.09.001"
DEMO_CONFIG_CONTENT: dict[str, Any] = {
    "analysis": {"default_window_days": 90, "top_n": 20},
    "heartbeat": {"interval_seconds": 60},
    "sync": {"pull_interval_seconds": 60, "max_retries": 5},
}

#: 演示调度任务：(task_type, cron_expr, scope, enabled)
DEMO_TASKS: tuple[tuple[str, str | None, dict[str, Any], bool], ...] = (
    ("ANALYSIS_INVENTORY_KPI", "0 8 * * *", {"feature": "inventory-kpi"}, True),
    ("ANALYSIS_ABC_AGING", "0 9 * * 1", {"feature": "abc-aging"}, True),
)


def build_memory_repositories(
    *,
    password: str | None = None,
    signing_secret: str = "",
    settings: Settings | None = None,
) -> MemoryRepositories:
    """构建并播种内存仓储（本地演示 / 测试注入入口）。

    ``settings`` 为组合根传入的运行配置（生产环境守卫与密钥解析都以它为准，
    避免与进程级全局配置漂移）；缺省时回退 ``get_settings()``。
    ``signing_secret`` 为配置签名密钥（M3）：显式传入用于与 ConfigService
    保持同一签名口径；缺省时从运行配置解析（非生产临时密钥，重启即失效）。
    """
    from app.settings import get_settings

    resolved = settings or get_settings()
    if resolved.is_production:
        raise RuntimeError("生产环境禁止使用内存仓储（CONTROL_PLANE_REPOSITORY=memory）。")

    repos = MemoryRepositories()
    seed_demo(
        repos,
        password=password or resolve_demo_password(),
        signing_secret=signing_secret or resolved.resolve_config_signing_secret(),
    )
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
    signing_secret: str = "",
) -> None:
    """写入一套可直接登录的演示数据：行业类型、商户、许可证、两个账号、功能授权、配置与任务。

    ``signing_secret`` 缺省时从运行配置解析（与内存演示进程的验签口径一致）。
    """
    if not signing_secret:
        from app.settings import get_settings

        signing_secret = get_settings().resolve_config_signing_secret()

    now = datetime.now(UTC)
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

    # M3：演示配置（PUBLISHED，带摘要与签名）与调度任务（含一个待拉取运行）
    config_service = ConfigService(repos.configs, signing_secret)
    config_service.publish(
        tenant_id=DEMO_TENANT_ID,
        version=DEMO_CONFIG_VERSION,
        content=dict(DEMO_CONFIG_CONTENT),
        published_by=DEMO_DEVELOPER_LOGIN,
        now=now,
    )
    _seed_demo_tasks(repos, now=now, today=today)


def _seed_demo_tasks(repos: MemoryRepositories, *, now: datetime, today: date) -> None:
    """播种演示调度任务，并为第一个任务生成一个待拉取（CREATED）运行。"""
    first_task_id: str | None = None
    for task_type, cron_expr, scope, enabled in DEMO_TASKS:
        task_id = new_id(PREFIX_TASK)
        first_task_id = first_task_id or task_id
        repos.tasks.add_task(
            Task(
                task_id=task_id,
                tenant_id=DEMO_TENANT_ID,
                task_type=task_type,
                cron_expr=cron_expr,
                scope=dict(scope),
                enabled=enabled,
                created_by=DEMO_DEVELOPER_LOGIN,
                created_at=now,
            )
        )
    if first_task_id is None:
        return
    repos.tasks.add_run(
        TaskRun(
            run_id=new_id(PREFIX_TASK_RUN),
            task_id=first_task_id,
            tenant_id=DEMO_TENANT_ID,
            status=RunStatus.CREATED,
            scheduled_at=datetime.combine(
                today + timedelta(days=1), time(hour=8), tzinfo=UTC
            ),
            created_at=now,
            updated_at=now,
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
