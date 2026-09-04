"""组合根（M2 + M3）：按配置装配仓储、事件中心与应用服务。

- 仓储实现由 ``CONTROL_PLANE_REPOSITORY`` 决定：``postgres``（生产路径，
  SQLAlchemy + Alembic 迁移）或 ``memory``（测试注入与本地无库演示）；
- 应用层只依赖 ``app.application.ports`` 协议，切换实现对用例零改动；
- 事件中心是进程内单例：路由（线程池）发布事件，SSE 生成器（事件循环）消费。

M3 追加装配：配置、任务、心跳与同步信封四组仓储及对应应用服务
（ConfigService / TaskService / HeartbeatService / SyncService）。

``create_app`` 把容器挂到 ``app.state.container``，``app.api.v1.deps`` 从这里取依赖；
测试可直接构造容器注入，不依赖 FastAPI。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import sessionmaker

from app.application.audit import AuditService
from app.application.auth_usecase import AuthService
from app.application.config_usecase import ConfigService
from app.application.device_usecase import DeviceService
from app.application.heartbeat_usecase import HeartbeatService
from app.application.license_usecase import EntitlementService
from app.application.ports import (
    AuditRepository,
    ConfigRepository,
    DeviceRepository,
    EntitlementRepository,
    HeartbeatRepository,
    IdentityRepository,
    SyncEnvelopeRepository,
    TaskRepository,
)
from app.application.sync_usecase import SyncService
from app.application.task_usecase import TaskService
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
    configs: ConfigRepository
    tasks: TaskRepository
    heartbeats: HeartbeatRepository
    sync_envelopes: SyncEnvelopeRepository
    hub: RealtimeHub
    audit: AuditService
    entitlement_service: EntitlementService
    auth: AuthService
    device_service: DeviceService
    config_service: ConfigService
    task_service: TaskService
    heartbeat_service: HeartbeatService
    sync_service: SyncService


def build_container(settings: Settings | None = None) -> Container:
    """按配置装配全部依赖。"""
    settings = settings or get_settings()
    hub = RealtimeHub()

    if settings.CONTROL_PLANE_REPOSITORY.strip().lower() == REPOSITORY_MEMORY:
        # 签名密钥显式传入：保证种子演示配置与 ConfigService 用同一口径签名
        repos = build_memory_repositories(settings=settings)
        identity: IdentityRepository = repos.identity
        devices: DeviceRepository = repos.devices
        entitlements: EntitlementRepository = repos.entitlements
        audit_repository: AuditRepository = repos.audit
        configs: ConfigRepository = repos.configs
        tasks: TaskRepository = repos.tasks
        heartbeats: HeartbeatRepository = repos.heartbeats
        sync_envelopes: SyncEnvelopeRepository = repos.sync_envelopes
    else:
        from app.infrastructure.db.repositories import (
            PostgresAuditRepository,
            PostgresConfigRepository,
            PostgresDeviceRepository,
            PostgresEntitlementRepository,
            PostgresHeartbeatRepository,
            PostgresIdentityRepository,
            PostgresSyncEnvelopeRepository,
            PostgresTaskRepository,
        )

        # 每个请求一个短会话：sessionmaker 惰性建连，连接超时沿用健康检查口径（3 秒）
        factory = sessionmaker(bind=build_engine(settings.DATABASE_URL), expire_on_commit=False)
        identity = PostgresIdentityRepository(factory)
        devices = PostgresDeviceRepository(factory)
        entitlements = PostgresEntitlementRepository(factory)
        audit_repository = PostgresAuditRepository(factory)
        configs = PostgresConfigRepository(factory)
        tasks = PostgresTaskRepository(factory)
        heartbeats = PostgresHeartbeatRepository(factory)
        sync_envelopes = PostgresSyncEnvelopeRepository(factory)

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
    config_service = ConfigService(configs, settings.resolve_config_signing_secret())
    task_service = TaskService(tasks, devices)
    heartbeat_service = HeartbeatService(heartbeats, devices, publisher=hub)
    sync_service = SyncService(sync_envelopes, devices, settings.resolve_sync_encryption_key())

    return Container(
        settings=settings,
        identity=identity,
        devices=devices,
        entitlements=entitlements,
        audit_repository=audit_repository,
        configs=configs,
        tasks=tasks,
        heartbeats=heartbeats,
        sync_envelopes=sync_envelopes,
        hub=hub,
        audit=audit,
        entitlement_service=entitlement_service,
        auth=auth,
        device_service=device_service,
        config_service=config_service,
        task_service=task_service,
        heartbeat_service=heartbeat_service,
        sync_service=sync_service,
    )
