"""组合根（M2）：按配置装配仓储、事件中心与应用服务。

- 仓储实现由 ``CONTROL_PLANE_REPOSITORY`` 决定：``postgres``（生产路径，
  SQLAlchemy + Alembic 迁移）或 ``memory``（测试注入与本地无库演示）；
- 应用层只依赖 ``app.application.ports`` 协议，切换实现对用例零改动；
- 事件中心是进程内单例：路由（线程池）发布事件，SSE 生成器（事件循环）消费。

``create_app`` 把容器挂到 ``app.state.container``，``app.api.v1.deps`` 从这里取依赖；
测试可直接构造容器注入，不依赖 FastAPI。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import sessionmaker

from app.application.audit import AuditService
from app.application.auth_usecase import AuthService
from app.application.device_usecase import DeviceService
from app.application.license_usecase import EntitlementService
from app.application.ports import (
    AuditRepository,
    DeviceRepository,
    EntitlementRepository,
    IdentityRepository,
)
from app.infrastructure.db.engine import build_engine
from app.infrastructure.memory.seed import build_memory_repositories
from app.infrastructure.realtime.hub import RealtimeHub
from app.settings import REPOSITORY_MEMORY, Settings, get_settings


@dataclass
class Container:
    """控制平面依赖容器（进程级单例）。"""

    settings: Settings
    identity: IdentityRepository
    devices: DeviceRepository
    entitlements: EntitlementRepository
    audit_repository: AuditRepository
    hub: RealtimeHub
    audit: AuditService
    entitlement_service: EntitlementService
    auth: AuthService
    device_service: DeviceService


def build_container(settings: Settings | None = None) -> Container:
    """按配置装配全部依赖。"""
    settings = settings or get_settings()
    hub = RealtimeHub()

    if settings.CONTROL_PLANE_REPOSITORY.strip().lower() == REPOSITORY_MEMORY:
        repos = build_memory_repositories()
        identity: IdentityRepository = repos.identity
        devices: DeviceRepository = repos.devices
        entitlements: EntitlementRepository = repos.entitlements
        audit_repository: AuditRepository = repos.audit
    else:
        from app.infrastructure.db.repositories import (
            PostgresAuditRepository,
            PostgresDeviceRepository,
            PostgresEntitlementRepository,
            PostgresIdentityRepository,
        )

        # 每个请求一个短会话：sessionmaker 惰性建连，连接超时沿用健康检查口径（3 秒）
        factory = sessionmaker(bind=build_engine(settings.DATABASE_URL), expire_on_commit=False)
        identity = PostgresIdentityRepository(factory)
        devices = PostgresDeviceRepository(factory)
        entitlements = PostgresEntitlementRepository(factory)
        audit_repository = PostgresAuditRepository(factory)

    audit = AuditService(audit_repository)
    entitlement_service = EntitlementService(
        entitlements, grace_days=settings.LICENSE_OFFLINE_GRACE_DAYS
    )
    auth = AuthService(
        identity,
        entitlement_service,
        audit,
        secret=settings.resolve_auth_secret(),
        publisher=hub,
    )
    device_service = DeviceService(devices, entitlement_service, audit, publisher=hub)

    return Container(
        settings=settings,
        identity=identity,
        devices=devices,
        entitlements=entitlements,
        audit_repository=audit_repository,
        hub=hub,
        audit=audit,
        entitlement_service=entitlement_service,
        auth=auth,
        device_service=device_service,
    )
