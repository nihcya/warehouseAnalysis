"""PostgreSQL 仓储实现（M2 + M3，生产路径）。

- 实现 ``app.application.ports`` 的八个仓储协议；语义与内存实现一致，
  差异只在事务与锁（每个方法一个短事务，写操作由数据库约束兜底）；
- 行 → domain 的转换集中在本文件的 ``_xxx_from_row``：
  未知枚举直接抛错，不静默降级（主基线 §35.5）；
- 时间与本地库不同：云端统一 ``TIMESTAMPTZ`` 存 UTC，故直接用 datetime 对象，
  不做 ISO 文本转换。

本机无 PostgreSQL 时本模块只被组合根按配置引用，不参与默认测试；
运行期正确性由 CI 的 PostgreSQL service 容器迁移测试与后续集成测试保证。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from app.domain.account import Account, AccountRole, AccountStatus
from app.domain.audit import AuditAction, AuditEntry, AuditResult
from app.domain.catalog import FeatureGrant, ProductProfile, ProductProfileStatus
from app.domain.config import ConfigStatus, ConfigVersion
from app.domain.device import Device, DeviceStatus, DeviceType
from app.domain.heartbeat import Heartbeat
from app.domain.license import License, LicenseStatus
from app.domain.session import ClientType
from app.domain.session import Session as DomainSession
from app.domain.sync import SyncEnvelope, SyncEnvelopeStatus
from app.domain.task import RunStatus, Task, TaskRun
from app.domain.tenant import Tenant, TenantStatus
from app.infrastructure.db.models import (
    AccountRow,
    AuditLogRow,
    ConfigVersionRow,
    DeviceRow,
    FeatureGrantRow,
    HeartbeatRow,
    LicenseRow,
    ProductProfileRow,
    SessionRow,
    SyncEnvelopeRow,
    TaskRow,
    TaskRunRow,
    TenantRow,
)


def utc_now() -> datetime:
    """当前 UTC 时间（云端所有时间统一 UTC）。"""
    return datetime.now(UTC)


def _tenant_from_row(row: TenantRow) -> Tenant:
    return Tenant(
        tenant_id=row.tenant_id,
        name=row.name,
        product_profile_id=row.product_profile_id,
        status=TenantStatus(row.status),
    )


def _account_from_row(row: AccountRow) -> Account:
    return Account(
        account_id=row.account_id,
        login_name=row.login_name,
        password_hash=row.password_hash,
        role=AccountRole(row.role),
        tenant_id=row.tenant_id,
        status=AccountStatus(row.status),
        failed_attempts=row.failed_attempts,
        locked_until=row.locked_until,
        last_login_at=row.last_login_at,
    )


def _session_from_row(row: SessionRow) -> DomainSession:
    return DomainSession(
        session_id=row.session_id,
        account_id=row.account_id,
        client_type=ClientType(row.client_type),
        refresh_token_hash=row.refresh_token_hash,
        expires_at=row.expires_at,
        device_id=row.device_id,
        previous_refresh_token_hash=row.previous_refresh_token_hash,
        rotated_at=row.rotated_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
    )


def _device_from_row(row: DeviceRow) -> Device:
    return Device(
        device_id=row.device_id,
        tenant_id=row.tenant_id,
        device_type=DeviceType(row.device_type),
        name=row.name,
        fingerprint=row.fingerprint,
        status=DeviceStatus(row.status),
        app_version=row.app_version,
        last_seen_at=row.last_seen_at,
        registered_at=row.registered_at,
        updated_at=row.updated_at,
    )


def _profile_from_row(row: ProductProfileRow) -> ProductProfile:
    return ProductProfile(
        product_profile_id=row.product_profile_id,
        code=row.code,
        name=row.name,
        status=ProductProfileStatus(row.status),
        default_config_version=row.default_config_version,
    )


def _license_from_row(row: LicenseRow) -> License:
    return License(
        license_id=row.license_id,
        tenant_id=row.tenant_id,
        product_profile_id=row.product_profile_id,
        starts_at=row.starts_at,
        expires_at=row.expires_at,
        max_devices=row.max_devices,
        status=LicenseStatus(row.status),
    )


def _grant_from_row(row: FeatureGrantRow) -> FeatureGrant:
    return FeatureGrant(
        feature_grant_id=row.feature_grant_id,
        tenant_id=row.tenant_id,
        feature_code=row.feature_code,
        enabled=row.enabled,
        source=row.source,
        expires_at=row.expires_at,
    )


def _audit_from_row(row: AuditLogRow) -> AuditEntry:
    return AuditEntry(
        audit_id=row.audit_id,
        action=AuditAction(row.action),
        result=AuditResult(row.result),
        occurred_at=row.occurred_at,
        actor_account_id=row.actor_account_id,
        actor_role=row.actor_role,
        tenant_id=row.tenant_id,
        target_type=row.target_type,
        target_id=row.target_id,
        request_id=row.request_id,
        detail=dict(row.detail_json or {}),
    )


def _config_from_row(row: ConfigVersionRow) -> ConfigVersion:
    return ConfigVersion(
        config_version_id=row.config_version_id,
        tenant_id=row.tenant_id,
        version=row.version,
        content=dict(row.content_json or {}),
        sha256=row.sha256,
        signature=row.signature,
        schema_version=row.schema_version,
        status=ConfigStatus(row.status),
        published_by=row.published_by,
        effective_at=row.effective_at,
        created_at=row.created_at,
    )


def _task_from_row(row: TaskRow) -> Task:
    return Task(
        task_id=row.task_id,
        tenant_id=row.tenant_id,
        task_type=row.task_type,
        cron_expr=row.cron_expr,
        scope=dict(row.scope_json or {}),
        enabled=row.enabled,
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _run_from_row(row: TaskRunRow) -> TaskRun:
    return TaskRun(
        run_id=row.run_id,
        task_id=row.task_id,
        tenant_id=row.tenant_id,
        status=RunStatus(row.status),
        device_id=row.device_id,
        scheduled_at=row.scheduled_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        error_code=row.error_code,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _heartbeat_from_row(row: HeartbeatRow) -> Heartbeat:
    return Heartbeat(
        device_id=row.device_id,
        tenant_id=row.tenant_id,
        sent_at=row.sent_at,
        status=row.status,
        app_version=row.app_version,
        engine_version=row.engine_version,
        db_schema_version=row.db_schema_version,
        pending_sync_count=row.pending_sync_count,
    )


def _envelope_from_row(row: SyncEnvelopeRow) -> SyncEnvelope:
    return SyncEnvelope(
        envelope_id=row.envelope_id,
        tenant_id=row.tenant_id,
        target_device_id=row.target_device_id,
        event_id=row.event_id,
        ciphertext=row.ciphertext,
        idempotency_key=row.idempotency_key,
        status=SyncEnvelopeStatus(row.status),
        expires_at=row.expires_at,
        created_at=row.created_at,
        acked_at=row.acked_at,
    )


class PostgresIdentityRepository:
    """tenant / account / session 仓储（PostgreSQL）。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    # ---- tenant ----
    def get_tenant(self, tenant_id: str) -> Tenant | None:
        with self._session_factory() as session:
            row = session.get(TenantRow, tenant_id)
            return _tenant_from_row(row) if row is not None else None

    def add_tenant(self, tenant: Tenant) -> None:
        now = utc_now()
        with self._session_factory() as session, session.begin():
            session.add(
                TenantRow(
                    tenant_id=tenant.tenant_id,
                    name=tenant.name,
                    product_profile_id=tenant.product_profile_id,
                    status=tenant.status.value,
                    created_at=now,
                    updated_at=now,
                )
            )

    # ---- account ----
    def get_account(self, account_id: str) -> Account | None:
        with self._session_factory() as session:
            row = session.get(AccountRow, account_id)
            return _account_from_row(row) if row is not None else None

    def get_account_by_login_name(self, login_name: str) -> Account | None:
        with self._session_factory() as session:
            row = session.execute(
                select(AccountRow).where(AccountRow.login_name == login_name)
            ).scalar_one_or_none()
            return _account_from_row(row) if row is not None else None

    def add_account(self, account: Account) -> None:
        now = utc_now()
        with self._session_factory() as session, session.begin():
            session.add(
                AccountRow(
                    account_id=account.account_id,
                    tenant_id=account.tenant_id,
                    login_name=account.login_name,
                    password_hash=account.password_hash,
                    role=account.role.value,
                    status=account.status.value,
                    failed_attempts=account.failed_attempts,
                    locked_until=account.locked_until,
                    last_login_at=account.last_login_at,
                    created_at=now,
                    updated_at=now,
                )
            )

    def save_account(self, account: Account) -> None:
        with self._session_factory() as session, session.begin():
            session.execute(
                update(AccountRow)
                .where(AccountRow.account_id == account.account_id)
                .values(
                    status=account.status.value,
                    failed_attempts=account.failed_attempts,
                    locked_until=account.locked_until,
                    last_login_at=account.last_login_at,
                    updated_at=utc_now(),
                )
            )

    # ---- session ----
    def get_session(self, session_id: str) -> DomainSession | None:
        with self._session_factory() as session:
            row = session.get(SessionRow, session_id)
            return _session_from_row(row) if row is not None else None

    def get_session_by_refresh_hash(self, token_hash: str) -> DomainSession | None:
        with self._session_factory() as session:
            row = session.execute(
                select(SessionRow).where(SessionRow.refresh_token_hash == token_hash)
            ).scalar_one_or_none()
            return _session_from_row(row) if row is not None else None

    def get_session_by_previous_refresh_hash(self, token_hash: str) -> DomainSession | None:
        with self._session_factory() as session:
            row = session.execute(
                select(SessionRow).where(SessionRow.previous_refresh_token_hash == token_hash)
            ).scalar_one_or_none()
            return _session_from_row(row) if row is not None else None

    def add_session(self, session: DomainSession) -> None:
        with self._session_factory() as session_, session_.begin():
            session_.add(
                SessionRow(
                    session_id=session.session_id,
                    account_id=session.account_id,
                    client_type=session.client_type.value,
                    device_id=session.device_id,
                    refresh_token_hash=session.refresh_token_hash,
                    previous_refresh_token_hash=session.previous_refresh_token_hash,
                    expires_at=session.expires_at,
                    rotated_at=session.rotated_at,
                    revoked_at=session.revoked_at,
                    created_at=session.created_at or utc_now(),
                )
            )

    def save_session(self, session: DomainSession) -> None:
        with self._session_factory() as session_, session_.begin():
            session_.execute(
                update(SessionRow)
                .where(SessionRow.session_id == session.session_id)
                .values(
                    refresh_token_hash=session.refresh_token_hash,
                    previous_refresh_token_hash=session.previous_refresh_token_hash,
                    # 轮换时应用层会重算 expires_at（auth_usecase._rotate），
                    # 必须一并落库，否则会话 TTL 不随刷新延长（Issue #17）。
                    expires_at=session.expires_at,
                    rotated_at=session.rotated_at,
                    revoked_at=session.revoked_at,
                )
            )

    def revoke_sessions_of_account(self, account_id: str, now: datetime) -> int:
        with self._session_factory() as session, session.begin():
            result = cast(
                "CursorResult[Any]",
                session.execute(
                    update(SessionRow)
                    .where(
                        SessionRow.account_id == account_id,
                        SessionRow.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                ),
            )
            return int(result.rowcount or 0)


class PostgresDeviceRepository:
    """device 仓储（PostgreSQL）。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_device(self, device_id: str) -> Device | None:
        with self._session_factory() as session:
            row = session.get(DeviceRow, device_id)
            return _device_from_row(row) if row is not None else None

    def get_device_by_fingerprint(self, tenant_id: str, fingerprint: str) -> Device | None:
        with self._session_factory() as session:
            row = session.execute(
                select(DeviceRow).where(
                    DeviceRow.tenant_id == tenant_id,
                    DeviceRow.fingerprint == fingerprint,
                )
            ).scalar_one_or_none()
            return _device_from_row(row) if row is not None else None

    def list_devices(self, tenant_id: str) -> list[Device]:
        with self._session_factory() as session:
            rows = session.execute(
                select(DeviceRow)
                .where(DeviceRow.tenant_id == tenant_id)
                .order_by(DeviceRow.registered_at, DeviceRow.device_id)
            ).scalars()
            return [_device_from_row(row) for row in rows]

    def add_device(self, device: Device) -> None:
        now = utc_now()
        with self._session_factory() as session, session.begin():
            session.add(
                DeviceRow(
                    device_id=device.device_id,
                    tenant_id=device.tenant_id,
                    device_type=device.device_type.value,
                    name=device.name,
                    fingerprint=device.fingerprint,
                    status=device.status.value,
                    app_version=device.app_version,
                    last_seen_at=device.last_seen_at,
                    registered_at=device.registered_at or now,
                    updated_at=device.updated_at or now,
                )
            )

    def save_device(self, device: Device) -> None:
        with self._session_factory() as session, session.begin():
            session.execute(
                update(DeviceRow)
                .where(DeviceRow.device_id == device.device_id)
                .values(
                    name=device.name,
                    device_type=device.device_type.value,
                    status=device.status.value,
                    app_version=device.app_version,
                    last_seen_at=device.last_seen_at,
                    updated_at=utc_now(),
                )
            )

    def count_registered_devices(self, tenant_id: str) -> int:
        with self._session_factory() as session:
            count = session.execute(
                select(func.count())
                .select_from(DeviceRow)
                .where(
                    DeviceRow.tenant_id == tenant_id,
                    DeviceRow.status != DeviceStatus.REVOKED.value,
                )
            ).scalar_one()
            return int(count)


class PostgresEntitlementRepository:
    """product_profile / license / feature_grant 仓储（PostgreSQL）。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_product_profile(self, product_profile_id: str) -> ProductProfile | None:
        with self._session_factory() as session:
            row = session.get(ProductProfileRow, product_profile_id)
            return _profile_from_row(row) if row is not None else None

    def add_product_profile(self, profile: ProductProfile) -> None:
        with self._session_factory() as session, session.begin():
            session.add(
                ProductProfileRow(
                    product_profile_id=profile.product_profile_id,
                    code=profile.code,
                    name=profile.name,
                    status=profile.status.value,
                    default_config_version=profile.default_config_version,
                    created_at=utc_now(),
                )
            )

    def get_license(self, license_id: str) -> License | None:
        with self._session_factory() as session:
            row = session.get(LicenseRow, license_id)
            return _license_from_row(row) if row is not None else None

    def get_active_license(self, tenant_id: str) -> License | None:
        with self._session_factory() as session:
            row = session.execute(
                select(LicenseRow).where(
                    LicenseRow.tenant_id == tenant_id,
                    LicenseRow.status == LicenseStatus.ACTIVE.value,
                )
            ).scalar_one_or_none()
            return _license_from_row(row) if row is not None else None

    def add_license(self, license_: License) -> None:
        now = utc_now()
        with self._session_factory() as session, session.begin():
            session.add(
                LicenseRow(
                    license_id=license_.license_id,
                    tenant_id=license_.tenant_id,
                    product_profile_id=license_.product_profile_id,
                    starts_at=license_.starts_at,
                    expires_at=license_.expires_at,
                    max_devices=license_.max_devices,
                    status=license_.status.value,
                    created_at=now,
                    updated_at=now,
                )
            )

    def save_license(self, license_: License) -> None:
        with self._session_factory() as session, session.begin():
            session.execute(
                update(LicenseRow)
                .where(LicenseRow.license_id == license_.license_id)
                .values(
                    starts_at=license_.starts_at,
                    expires_at=license_.expires_at,
                    max_devices=license_.max_devices,
                    status=license_.status.value,
                    updated_at=utc_now(),
                )
            )

    def list_feature_grants(self, tenant_id: str) -> list[FeatureGrant]:
        with self._session_factory() as session:
            rows = session.execute(
                select(FeatureGrantRow).where(
                    FeatureGrantRow.tenant_id == tenant_id,
                    FeatureGrantRow.enabled.is_(True),
                )
            ).scalars()
            return [_grant_from_row(row) for row in rows]

    def add_feature_grant(self, grant: FeatureGrant) -> None:
        with self._session_factory() as session, session.begin():
            session.add(
                FeatureGrantRow(
                    feature_grant_id=grant.feature_grant_id,
                    tenant_id=grant.tenant_id,
                    feature_code=grant.feature_code,
                    enabled=grant.enabled,
                    source=grant.source,
                    expires_at=grant.expires_at,
                    created_at=utc_now(),
                )
            )


class PostgresAuditRepository:
    """audit_log 仓储（PostgreSQL，只追加）。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add(self, entry: AuditEntry) -> None:
        with self._session_factory() as session, session.begin():
            session.add(
                AuditLogRow(
                    audit_id=entry.audit_id,
                    actor_account_id=entry.actor_account_id,
                    actor_role=entry.actor_role,
                    tenant_id=entry.tenant_id,
                    action=entry.action.value,
                    target_type=entry.target_type,
                    target_id=entry.target_id,
                    result=entry.result.value,
                    request_id=entry.request_id,
                    detail_json=entry.detail or None,
                    occurred_at=entry.occurred_at,
                )
            )

    def list_for_tenant(self, tenant_id: str, limit: int = 100) -> list[AuditEntry]:
        with self._session_factory() as session:
            rows = session.execute(
                select(AuditLogRow)
                .where(AuditLogRow.tenant_id == tenant_id)
                .order_by(AuditLogRow.occurred_at.desc())
                .limit(limit)
            ).scalars()
            return [_audit_from_row(row) for row in rows]

    def list_for_actor(self, account_id: str, limit: int = 100) -> list[AuditEntry]:
        with self._session_factory() as session:
            rows = session.execute(
                select(AuditLogRow)
                .where(AuditLogRow.actor_account_id == account_id)
                .order_by(AuditLogRow.occurred_at.desc())
                .limit(limit)
            ).scalars()
            return [_audit_from_row(row) for row in rows]


class PostgresConfigRepository:
    """config_version 仓储（PostgreSQL，M3）。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_effective_config(self, tenant_id: str) -> ConfigVersion | None:
        with self._session_factory() as session:
            row = session.execute(
                select(ConfigVersionRow)
                .where(
                    ConfigVersionRow.tenant_id == tenant_id,
                    ConfigVersionRow.status == ConfigStatus.PUBLISHED.value,
                )
                .order_by(
                    ConfigVersionRow.effective_at.desc().nullslast(),
                    ConfigVersionRow.created_at.desc(),
                )
                .limit(1)
            ).scalar_one_or_none()
            return _config_from_row(row) if row is not None else None

    def add_config(self, config: ConfigVersion) -> None:
        with self._session_factory() as session, session.begin():
            session.add(
                ConfigVersionRow(
                    config_version_id=config.config_version_id,
                    tenant_id=config.tenant_id,
                    version=config.version,
                    content_json=dict(config.content),
                    sha256=config.sha256,
                    signature=config.signature,
                    schema_version=config.schema_version,
                    status=config.status.value,
                    published_by=config.published_by,
                    effective_at=config.effective_at,
                    created_at=config.created_at or utc_now(),
                )
            )


class PostgresTaskRepository:
    """task / task_run 仓储（PostgreSQL，M3）。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_task(self, task_id: str) -> Task | None:
        with self._session_factory() as session:
            row = session.get(TaskRow, task_id)
            return _task_from_row(row) if row is not None else None

    def list_tasks(self, tenant_id: str) -> list[Task]:
        with self._session_factory() as session:
            rows = session.execute(
                select(TaskRow)
                .where(TaskRow.tenant_id == tenant_id)
                .order_by(TaskRow.created_at, TaskRow.task_id)
            ).scalars()
            return [_task_from_row(row) for row in rows]

    def add_task(self, task: Task) -> None:
        with self._session_factory() as session, session.begin():
            session.add(
                TaskRow(
                    task_id=task.task_id,
                    tenant_id=task.tenant_id,
                    task_type=task.task_type,
                    cron_expr=task.cron_expr,
                    scope_json=dict(task.scope),
                    enabled=task.enabled,
                    created_by=task.created_by,
                    created_at=task.created_at or utc_now(),
                )
            )

    def add_run(self, run: TaskRun) -> None:
        now = utc_now()
        with self._session_factory() as session, session.begin():
            session.add(
                TaskRunRow(
                    run_id=run.run_id,
                    task_id=run.task_id,
                    tenant_id=run.tenant_id,
                    device_id=run.device_id,
                    status=run.status.value,
                    scheduled_at=run.scheduled_at,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    error_code=run.error_code,
                    created_at=run.created_at or now,
                    updated_at=run.updated_at or now,
                )
            )

    def save_run(self, run: TaskRun) -> None:
        with self._session_factory() as session, session.begin():
            session.execute(
                update(TaskRunRow)
                .where(TaskRunRow.run_id == run.run_id)
                .values(
                    status=run.status.value,
                    device_id=run.device_id,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    error_code=run.error_code,
                    updated_at=utc_now(),
                )
            )

    def pull_runs_for_device(
        self, *, tenant_id: str, device_id: str, limit: int, now: datetime
    ) -> list[TaskRun]:
        """单事务内锁定：候选 CRETED 运行 UPDATE 为 QUEUED（已分发）。

        UPDATE 复带 ``status = CREATED`` 条件，即使候选集在 SELECT 与 UPDATE
        之间被并发修改，也不会重复分发；返回顺序与候选集一致（scheduled_at 升序）。
        """
        with self._session_factory() as session, session.begin():
            candidate_ids = list(
                session.execute(
                    select(TaskRunRow.run_id)
                    .where(
                        TaskRunRow.tenant_id == tenant_id,
                        TaskRunRow.status == RunStatus.CREATED.value,
                        or_(
                            TaskRunRow.device_id.is_(None),
                            TaskRunRow.device_id == device_id,
                        ),
                    )
                    .order_by(
                        TaskRunRow.scheduled_at.asc().nullslast(),
                        TaskRunRow.created_at.asc(),
                    )
                    .limit(limit)
                ).scalars()
            )
            if not candidate_ids:
                return []
            rows = (
                session.execute(
                    update(TaskRunRow)
                    .where(
                        TaskRunRow.run_id.in_(candidate_ids),
                        TaskRunRow.status == RunStatus.CREATED.value,
                    )
                    .values(
                        status=RunStatus.QUEUED.value,
                        device_id=device_id,
                        updated_at=now,
                    )
                    .returning(TaskRunRow)
                )
                .scalars()
                .all()
            )
        by_id = {row.run_id: _run_from_row(row) for row in rows}
        return [by_id[run_id] for run_id in candidate_ids if run_id in by_id]


class PostgresHeartbeatRepository:
    """heartbeat 仓储（PostgreSQL，M3，device_id 主键 upsert）。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get(self, device_id: str) -> Heartbeat | None:
        with self._session_factory() as session:
            row = session.get(HeartbeatRow, device_id)
            return _heartbeat_from_row(row) if row is not None else None

    def upsert(self, heartbeat: Heartbeat) -> Heartbeat:
        values: dict[str, Any] = {
            "device_id": heartbeat.device_id,
            "tenant_id": heartbeat.tenant_id,
            "sent_at": heartbeat.sent_at,
            "status": heartbeat.status,
            "app_version": heartbeat.app_version,
            "engine_version": heartbeat.engine_version,
            "db_schema_version": heartbeat.db_schema_version,
            "pending_sync_count": heartbeat.pending_sync_count,
            "updated_at": heartbeat.sent_at,
        }
        insert_stmt = pg_insert(HeartbeatRow).values(**values)
        statement = insert_stmt.on_conflict_do_update(
            constraint="pk_heartbeat",
            set_={column: insert_stmt.excluded[column] for column in values
                  if column != "device_id"},
        ).returning(HeartbeatRow)
        with self._session_factory() as session, session.begin():
            row = session.execute(statement).scalar_one()
            return _heartbeat_from_row(row)


class PostgresSyncEnvelopeRepository:
    """sync_envelope 仓储（PostgreSQL，M3）。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add(self, envelope: SyncEnvelope) -> None:
        with self._session_factory() as session, session.begin():
            session.add(
                SyncEnvelopeRow(
                    envelope_id=envelope.envelope_id,
                    tenant_id=envelope.tenant_id,
                    target_device_id=envelope.target_device_id,
                    event_id=envelope.event_id,
                    ciphertext=envelope.ciphertext,
                    idempotency_key=envelope.idempotency_key,
                    status=envelope.status.value,
                    expires_at=envelope.expires_at,
                    created_at=envelope.created_at or utc_now(),
                    acked_at=envelope.acked_at,
                )
            )

    def get(self, envelope_id: str) -> SyncEnvelope | None:
        with self._session_factory() as session:
            row = session.get(SyncEnvelopeRow, envelope_id)
            return _envelope_from_row(row) if row is not None else None

    def get_by_event_id(self, event_id: str) -> SyncEnvelope | None:
        with self._session_factory() as session:
            row = session.execute(
                select(SyncEnvelopeRow).where(SyncEnvelopeRow.event_id == event_id)
            ).scalar_one_or_none()
            return _envelope_from_row(row) if row is not None else None

    def list_enqueued(
        self, *, tenant_id: str, target_device_id: str, limit: int, now: datetime
    ) -> list[SyncEnvelope]:
        with self._session_factory() as session:
            rows = session.execute(
                select(SyncEnvelopeRow)
                .where(
                    SyncEnvelopeRow.tenant_id == tenant_id,
                    SyncEnvelopeRow.target_device_id == target_device_id,
                    SyncEnvelopeRow.status == SyncEnvelopeStatus.ENQUEUED.value,
                    or_(
                        SyncEnvelopeRow.expires_at.is_(None),
                        SyncEnvelopeRow.expires_at > now,
                    ),
                )
                .order_by(SyncEnvelopeRow.created_at.asc())
                .limit(limit)
            ).scalars()
            return [_envelope_from_row(row) for row in rows]

    def mark_acked(self, envelope_id: str, now: datetime) -> SyncEnvelope | None:
        with self._session_factory() as session, session.begin():
            row = session.execute(
                update(SyncEnvelopeRow)
                .where(
                    SyncEnvelopeRow.envelope_id == envelope_id,
                    SyncEnvelopeRow.status != SyncEnvelopeStatus.ACKED.value,
                )
                .values(
                    status=SyncEnvelopeStatus.ACKED.value,
                    acked_at=now,
                )
                .returning(SyncEnvelopeRow)
            ).scalar_one_or_none()
        if row is not None:
            return _envelope_from_row(row)
        # 已被并发置为 ACKED（或不存在）：返回当前投影，由调用方判定幂等语义
        return self.get(envelope_id)

    def delete_expired(self, now: datetime) -> int:
        with self._session_factory() as session, session.begin():
            result = cast(
                "CursorResult[Any]",
                session.execute(
                    delete(SyncEnvelopeRow).where(SyncEnvelopeRow.expires_at <= now)
                ),
            )
            return int(result.rowcount or 0)
