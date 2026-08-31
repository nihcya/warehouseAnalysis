"""内存仓储实现：Identity / Device / Entitlement / Audit 四组。

与 PostgreSQL 实现保持同一套协议（``app.application.ports``）：
唯一约束、吊销过滤、只追加审计在本实现内同样生效，
因此同一组用例在两种实现下行为一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.account import Account
from app.domain.audit import AuditEntry
from app.domain.catalog import FeatureGrant, ProductProfile
from app.domain.device import Device, DeviceStatus
from app.domain.license import License, LicenseStatus
from app.domain.session import Session
from app.domain.tenant import Tenant


@dataclass
class MemoryStore:
    """进程内共享存储：四个仓储共用同一份数据，便于整体重置。"""

    tenants: dict[str, Tenant] = field(default_factory=dict)
    accounts: dict[str, Account] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)
    devices: dict[str, Device] = field(default_factory=dict)
    product_profiles: dict[str, ProductProfile] = field(default_factory=dict)
    licenses: dict[str, License] = field(default_factory=dict)
    feature_grants: dict[str, FeatureGrant] = field(default_factory=dict)
    audit_log: list[AuditEntry] = field(default_factory=list)

    def reset(self) -> None:
        """清空全部数据（测试隔离用）。"""
        self.tenants.clear()
        self.accounts.clear()
        self.sessions.clear()
        self.devices.clear()
        self.product_profiles.clear()
        self.licenses.clear()
        self.feature_grants.clear()
        self.audit_log.clear()


class MemoryIdentityRepository:
    """内存实现的商户 / 账号 / 会话仓储。"""

    def __init__(self, store: MemoryStore | None = None) -> None:
        self._store = store if store is not None else MemoryStore()

    @property
    def store(self) -> MemoryStore:
        """共享存储（供测试断言与种子数据使用）。"""
        return self._store

    # ---- tenant ----
    def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self._store.tenants.get(tenant_id)

    def add_tenant(self, tenant: Tenant) -> None:
        if tenant.tenant_id in self._store.tenants:
            raise ValueError(f"商户已存在：{tenant.tenant_id}")
        self._store.tenants[tenant.tenant_id] = tenant

    # ---- account ----
    def get_account(self, account_id: str) -> Account | None:
        return self._store.accounts.get(account_id)

    def get_account_by_login_name(self, login_name: str) -> Account | None:
        for account in self._store.accounts.values():
            if account.login_name == login_name:
                return account
        return None

    def add_account(self, account: Account) -> None:
        if account.account_id in self._store.accounts:
            raise ValueError(f"账号已存在：{account.account_id}")
        if self.get_account_by_login_name(account.login_name) is not None:
            raise ValueError(f"登录名已存在：{account.login_name}")
        self._store.accounts[account.account_id] = account

    def save_account(self, account: Account) -> None:
        self._store.accounts[account.account_id] = account

    # ---- session ----
    def get_session(self, session_id: str) -> Session | None:
        return self._store.sessions.get(session_id)

    def get_session_by_refresh_hash(self, token_hash: str) -> Session | None:
        for session in self._store.sessions.values():
            if session.refresh_token_hash == token_hash:
                return session
        return None

    def get_session_by_previous_refresh_hash(self, token_hash: str) -> Session | None:
        for session in self._store.sessions.values():
            if session.previous_refresh_token_hash == token_hash:
                return session
        return None

    def add_session(self, session: Session) -> None:
        self._store.sessions[session.session_id] = session

    def save_session(self, session: Session) -> None:
        self._store.sessions[session.session_id] = session

    def revoke_sessions_of_account(self, account_id: str, now: datetime) -> int:
        revoked = 0
        for session in self._store.sessions.values():
            if session.account_id == account_id and session.revoked_at is None:
                session.revoke(now)
                revoked += 1
        return revoked


class MemoryDeviceRepository:
    """内存实现的设备仓储。"""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def get_device(self, device_id: str) -> Device | None:
        return self._store.devices.get(device_id)

    def get_device_by_fingerprint(self, tenant_id: str, fingerprint: str) -> Device | None:
        for device in self._store.devices.values():
            if device.tenant_id == tenant_id and device.fingerprint == fingerprint:
                return device
        return None

    def list_devices(self, tenant_id: str) -> list[Device]:
        return [
            device
            for device in self._store.devices.values()
            if device.tenant_id == tenant_id
        ]

    def add_device(self, device: Device) -> None:
        if device.device_id in self._store.devices:
            raise ValueError(f"设备已存在：{device.device_id}")
        self._store.devices[device.device_id] = device

    def save_device(self, device: Device) -> None:
        self._store.devices[device.device_id] = device

    def count_registered_devices(self, tenant_id: str) -> int:
        return sum(
            1
            for device in self._store.devices.values()
            if device.tenant_id == tenant_id and device.status is not DeviceStatus.REVOKED
        )


class MemoryEntitlementRepository:
    """内存实现的行业类型 / 许可证 / 功能授权仓储。"""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def get_product_profile(self, product_profile_id: str) -> ProductProfile | None:
        return self._store.product_profiles.get(product_profile_id)

    def add_product_profile(self, profile: ProductProfile) -> None:
        self._store.product_profiles[profile.product_profile_id] = profile

    def get_license(self, license_id: str) -> License | None:
        return self._store.licenses.get(license_id)

    def get_active_license(self, tenant_id: str) -> License | None:
        for license_ in self._store.licenses.values():
            if license_.tenant_id == tenant_id and license_.status is LicenseStatus.ACTIVE:
                return license_
        return None

    def add_license(self, license_: License) -> None:
        self._store.licenses[license_.license_id] = license_

    def save_license(self, license_: License) -> None:
        self._store.licenses[license_.license_id] = license_

    def list_feature_grants(self, tenant_id: str) -> list[FeatureGrant]:
        return [
            grant
            for grant in self._store.feature_grants.values()
            if grant.tenant_id == tenant_id and grant.enabled
        ]

    def add_feature_grant(self, grant: FeatureGrant) -> None:
        self._store.feature_grants[grant.feature_grant_id] = grant


class MemoryAuditRepository:
    """内存实现的审计仓储（只追加）。"""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def add(self, entry: AuditEntry) -> None:
        self._store.audit_log.append(entry)

    def list_for_tenant(self, tenant_id: str, limit: int = 100) -> list[AuditEntry]:
        entries = [e for e in self._store.audit_log if e.tenant_id == tenant_id]
        return entries[-limit:]

    def list_for_actor(self, account_id: str, limit: int = 100) -> list[AuditEntry]:
        entries = [e for e in self._store.audit_log if e.actor_account_id == account_id]
        return entries[-limit:]


@dataclass
class MemoryRepositories:
    """内存仓储集合：组合根按配置整体注入应用层（四个仓储共享同一 store）。"""

    store: MemoryStore = field(default_factory=MemoryStore)

    def __post_init__(self) -> None:
        self.identity: MemoryIdentityRepository = MemoryIdentityRepository(self.store)
        self.devices: MemoryDeviceRepository = MemoryDeviceRepository(self.store)
        self.entitlements: MemoryEntitlementRepository = MemoryEntitlementRepository(self.store)
        self.audit: MemoryAuditRepository = MemoryAuditRepository(self.store)
