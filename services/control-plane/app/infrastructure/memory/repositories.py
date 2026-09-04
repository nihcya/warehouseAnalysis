"""内存仓储实现：Identity / Device / Entitlement / Audit / Config / Task / Heartbeat / Sync 八组。

与 PostgreSQL 实现保持同一套协议（``app.application.ports``）：
唯一约束、吊销过滤、只追加审计在本实现内同样生效，
因此同一组用例在两种实现下行为一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.domain.account import Account
from app.domain.audit import AuditEntry
from app.domain.catalog import FeatureGrant, ProductProfile
from app.domain.config import ConfigStatus, ConfigVersion
from app.domain.device import Device, DeviceStatus
from app.domain.heartbeat import Heartbeat
from app.domain.license import License, LicenseStatus
from app.domain.session import Session
from app.domain.sync import SyncEnvelope, SyncEnvelopeStatus
from app.domain.task import RunStatus, Task, TaskRun
from app.domain.tenant import Tenant

#: 排序兜底时间：scheduled_at / created_at 均缺失时排在最后
_DATETIME_FLOOR = datetime.min.replace(tzinfo=UTC)


@dataclass
class MemoryStore:
    """进程内共享存储：八个仓储共用同一份数据，便于整体重置。"""

    tenants: dict[str, Tenant] = field(default_factory=dict)
    accounts: dict[str, Account] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)
    devices: dict[str, Device] = field(default_factory=dict)
    product_profiles: dict[str, ProductProfile] = field(default_factory=dict)
    licenses: dict[str, License] = field(default_factory=dict)
    feature_grants: dict[str, FeatureGrant] = field(default_factory=dict)
    audit_log: list[AuditEntry] = field(default_factory=list)
    config_versions: dict[str, ConfigVersion] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)
    task_runs: dict[str, TaskRun] = field(default_factory=dict)
    heartbeats: dict[str, Heartbeat] = field(default_factory=dict)
    sync_envelopes: dict[str, SyncEnvelope] = field(default_factory=dict)

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
        self.config_versions.clear()
        self.tasks.clear()
        self.task_runs.clear()
        self.heartbeats.clear()
        self.sync_envelopes.clear()


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
    """内存仓储集合：组合根按配置整体注入应用层（八个仓储共享同一 store）。"""

    store: MemoryStore = field(default_factory=MemoryStore)

    def __post_init__(self) -> None:
        self.identity: MemoryIdentityRepository = MemoryIdentityRepository(self.store)
        self.devices: MemoryDeviceRepository = MemoryDeviceRepository(self.store)
        self.entitlements: MemoryEntitlementRepository = MemoryEntitlementRepository(self.store)
        self.audit: MemoryAuditRepository = MemoryAuditRepository(self.store)
        self.configs: MemoryConfigRepository = MemoryConfigRepository(self.store)
        self.tasks: MemoryTaskRepository = MemoryTaskRepository(self.store)
        self.heartbeats: MemoryHeartbeatRepository = MemoryHeartbeatRepository(self.store)
        self.sync_envelopes: MemorySyncEnvelopeRepository = MemorySyncEnvelopeRepository(
            self.store
        )


class MemoryConfigRepository:
    """内存实现的配置版本仓储。"""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def get_effective_config(self, tenant_id: str) -> ConfigVersion | None:
        published = [
            config
            for config in self._store.config_versions.values()
            if config.tenant_id == tenant_id
            and config.status is ConfigStatus.PUBLISHED
            and config.effective_at is not None
        ]
        if not published:
            return None
        return max(published, key=lambda config: (config.effective_at, config.created_at))

    def add_config(self, config: ConfigVersion) -> None:
        for existing in self._store.config_versions.values():
            if existing.tenant_id == config.tenant_id and existing.version == config.version:
                raise ValueError(
                    f"配置版本已存在：{config.tenant_id} / {config.version}"
                )
        self._store.config_versions[config.config_version_id] = config


class MemoryTaskRepository:
    """内存实现的调度任务与运行投影仓储。"""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def get_task(self, task_id: str) -> Task | None:
        return self._store.tasks.get(task_id)

    def list_tasks(self, tenant_id: str) -> list[Task]:
        return [
            task
            for task in self._store.tasks.values()
            if task.tenant_id == tenant_id
        ]

    def add_task(self, task: Task) -> None:
        if task.task_id in self._store.tasks:
            raise ValueError(f"任务已存在：{task.task_id}")
        self._store.tasks[task.task_id] = task

    def add_run(self, run: TaskRun) -> None:
        if run.run_id in self._store.task_runs:
            raise ValueError(f"任务运行已存在：{run.run_id}")
        self._store.task_runs[run.run_id] = run

    def save_run(self, run: TaskRun) -> None:
        self._store.task_runs[run.run_id] = run

    def pull_runs_for_device(
        self, *, tenant_id: str, device_id: str, limit: int, now: datetime
    ) -> list[TaskRun]:
        candidates = sorted(
            (
                run
                for run in self._store.task_runs.values()
                if run.tenant_id == tenant_id
                and run.status is RunStatus.CREATED
                and (run.device_id is None or run.device_id == device_id)
            ),
            key=lambda run: run.scheduled_at or run.created_at or _DATETIME_FLOOR,
        )
        pulled: list[TaskRun] = []
        for run in candidates[:limit]:
            run.transition(RunStatus.QUEUED)
            run.device_id = device_id
            run.updated_at = now
            pulled.append(run)
        return pulled


class MemoryHeartbeatRepository:
    """内存实现的设备心跳投影仓储（device_id 主键 upsert）。"""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def get(self, device_id: str) -> Heartbeat | None:
        return self._store.heartbeats.get(device_id)

    def upsert(self, heartbeat: Heartbeat) -> Heartbeat:
        self._store.heartbeats[heartbeat.device_id] = heartbeat
        return heartbeat


class MemorySyncEnvelopeRepository:
    """内存实现的同步信封仓储。"""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def add(self, envelope: SyncEnvelope) -> None:
        if envelope.envelope_id in self._store.sync_envelopes:
            raise ValueError(f"信封已存在：{envelope.envelope_id}")
        for existing in self._store.sync_envelopes.values():
            if existing.event_id == envelope.event_id:
                raise ValueError(f"event_id 已存在：{envelope.event_id}")
        self._store.sync_envelopes[envelope.envelope_id] = envelope

    def get(self, envelope_id: str) -> SyncEnvelope | None:
        return self._store.sync_envelopes.get(envelope_id)

    def get_by_event_id(self, event_id: str) -> SyncEnvelope | None:
        for envelope in self._store.sync_envelopes.values():
            if envelope.event_id == event_id:
                return envelope
        return None

    def list_enqueued(
        self, *, tenant_id: str, target_device_id: str, limit: int, now: datetime
    ) -> list[SyncEnvelope]:
        envelopes = sorted(
            (
                envelope
                for envelope in self._store.sync_envelopes.values()
                if envelope.tenant_id == tenant_id
                and envelope.target_device_id == target_device_id
                and envelope.status is SyncEnvelopeStatus.ENQUEUED
                and (envelope.expires_at is None or envelope.expires_at > now)
            ),
            key=lambda envelope: envelope.created_at or _DATETIME_FLOOR,
        )
        return envelopes[:limit]

    def mark_acked(self, envelope_id: str, now: datetime) -> SyncEnvelope | None:
        envelope = self._store.sync_envelopes.get(envelope_id)
        if envelope is None or envelope.status is SyncEnvelopeStatus.ACKED:
            return envelope
        envelope.status = SyncEnvelopeStatus.ACKED
        envelope.acked_at = now
        return envelope

    def delete_expired(self, now: datetime) -> int:
        expired = [
            envelope_id
            for envelope_id, envelope in self._store.sync_envelopes.items()
            if envelope.expires_at is not None and envelope.expires_at <= now
        ]
        for envelope_id in expired:
            del self._store.sync_envelopes[envelope_id]
        return len(expired)
