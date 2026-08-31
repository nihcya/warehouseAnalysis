"""PostgreSQL 仓储实现（M2，生产路径）。

- 实现 ``app.application.ports`` 的四个仓储协议；语义与内存实现一致，
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

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from app.domain.account import Account, AccountRole, AccountStatus
from app.domain.audit import AuditAction, AuditEntry, AuditResult
from app.domain.catalog import FeatureGrant, ProductProfile, ProductProfileStatus
from app.domain.device import Device, DeviceStatus, DeviceType
from app.domain.license import License, LicenseStatus
from app.domain.session import ClientType
from app.domain.session import Session as DomainSession
from app.domain.tenant import Tenant, TenantStatus
from app.infrastructure.db.models import (
    AccountRow,
    AuditLogRow,
    DeviceRow,
    FeatureGrantRow,
    LicenseRow,
    ProductProfileRow,
    SessionRow,
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
        starts_at=row.starts_at.date(),
        expires_at=row.expires_at.date(),
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
