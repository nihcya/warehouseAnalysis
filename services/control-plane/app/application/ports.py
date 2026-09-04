"""应用层端口（仓储协议与事件发布协议，M2）。

- 应用层用例只依赖这些协议，不依赖 SQLAlchemy、psycopg 或具体存储，
  便于用内存实现做单元测试、用 PostgreSQL 实现跑生产（主基线 §30.3：
  API 层不直接写 ORM，Domain 层不依赖 FastAPI / SQLAlchemy）；
- 仓储实现按 ``CONTROL_PLANE_REPOSITORY`` 在组合根注入（``app/dependencies.py``）；
- 事件发布端口把"写操作 → 状态流"解耦，SSE 与轮询共用同一事件中心。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from app.domain.account import Account
from app.domain.audit import AuditEntry
from app.domain.catalog import FeatureGrant, ProductProfile
from app.domain.config import ConfigVersion
from app.domain.device import Device
from app.domain.heartbeat import Heartbeat
from app.domain.license import License
from app.domain.session import Session
from app.domain.sync import SyncEnvelope
from app.domain.task import Task, TaskRun
from app.domain.tenant import Tenant


class IdentityRepository(Protocol):
    """商户、账号与会话仓储。"""

    # ---- tenant ----
    def get_tenant(self, tenant_id: str) -> Tenant | None: ...
    def add_tenant(self, tenant: Tenant) -> None: ...

    # ---- account ----
    def get_account(self, account_id: str) -> Account | None: ...
    def get_account_by_login_name(self, login_name: str) -> Account | None: ...
    def add_account(self, account: Account) -> None: ...
    def save_account(self, account: Account) -> None: ...

    # ---- session ----
    def get_session(self, session_id: str) -> Session | None: ...
    def get_session_by_refresh_hash(self, token_hash: str) -> Session | None: ...
    def get_session_by_previous_refresh_hash(self, token_hash: str) -> Session | None:
        """按"刚被轮换掉"的指纹查找会话（Refresh Token 重放检测，见 domain/session.py）。"""
        ...
    def add_session(self, session: Session) -> None: ...
    def save_session(self, session: Session) -> None: ...
    def revoke_sessions_of_account(self, account_id: str, now: datetime) -> int:
        """撤销该账号全部有效会话，返回被撤销条数（重放检测与注销使用）。"""
        ...


class DeviceRepository(Protocol):
    """设备仓储。"""

    def get_device(self, device_id: str) -> Device | None: ...
    def get_device_by_fingerprint(self, tenant_id: str, fingerprint: str) -> Device | None: ...
    def list_devices(self, tenant_id: str) -> list[Device]: ...
    def add_device(self, device: Device) -> None: ...
    def save_device(self, device: Device) -> None: ...
    def count_registered_devices(self, tenant_id: str) -> int:
        """在册（未吊销）设备数，用于许可证 max_devices 校验。"""
        ...


class EntitlementRepository(Protocol):
    """行业类型、许可证与功能授权仓储。"""

    def get_product_profile(self, product_profile_id: str) -> ProductProfile | None: ...
    def add_product_profile(self, profile: ProductProfile) -> None: ...
    def get_license(self, license_id: str) -> License | None: ...
    def get_active_license(self, tenant_id: str) -> License | None: ...
    def add_license(self, license_: License) -> None: ...
    def save_license(self, license_: License) -> None: ...
    def list_feature_grants(self, tenant_id: str) -> list[FeatureGrant]: ...
    def add_feature_grant(self, grant: FeatureGrant) -> None: ...


class AuditRepository(Protocol):
    """审计仓储（只追加，不提供更新与删除入口）。"""

    def add(self, entry: AuditEntry) -> None: ...
    def list_for_tenant(self, tenant_id: str, limit: int = 100) -> Sequence[AuditEntry]: ...
    def list_for_actor(self, account_id: str, limit: int = 100) -> Sequence[AuditEntry]: ...


class EventPublisher(Protocol):
    """状态流事件发布端口（SSE 主通道与轮询降级共用同一事件中心）。"""

    def publish(self, tenant_id: str | None, event_type: str, payload: dict[str, Any]) -> str:
        """发布一条事件，返回事件 ID（供 SSE 的 ``id:`` 与 Last-Event-ID 续传）。"""
        ...


class ConfigRepository(Protocol):
    """配置版本仓储（M3）。"""

    def get_effective_config(self, tenant_id: str) -> ConfigVersion | None:
        """返回商户生效配置（PUBLISHED 中 effective_at 最新的一条）。"""
        ...

    def add_config(self, config: ConfigVersion) -> None:
        """写入新版本；``(tenant_id, version)`` 重复由实现层抛错（版本不可覆盖）。"""
        ...


class TaskRepository(Protocol):
    """调度任务与运行投影仓储（M3）。"""

    def get_task(self, task_id: str) -> Task | None: ...
    def list_tasks(self, tenant_id: str) -> list[Task]: ...
    def add_task(self, task: Task) -> None: ...
    def add_run(self, run: TaskRun) -> None: ...
    def save_run(self, run: TaskRun) -> None: ...
    def pull_runs_for_device(
        self, *, tenant_id: str, device_id: str, limit: int, now: datetime
    ) -> list[TaskRun]:
        """设备拉取待执行任务：CREATED 运行原子锁定为 QUEUED（已分发）。

        只返回本租户、目标为本设备（或未指定设备）的 CREATED 运行，
        拉取即锁定（锁定后不再被重复分发），按 scheduled_at 升序。
        """
        ...


class HeartbeatRepository(Protocol):
    """设备心跳最新投影仓储（M3，device_id 主键 upsert）。"""

    def get(self, device_id: str) -> Heartbeat | None: ...
    def upsert(self, heartbeat: Heartbeat) -> Heartbeat:
        """按 device_id upsert 最新投影，返回落库后的心跳。"""
        ...


class SyncEnvelopeRepository(Protocol):
    """同步信封仓储（M3，密文中继）。"""

    def add(self, envelope: SyncEnvelope) -> None: ...
    def get(self, envelope_id: str) -> SyncEnvelope | None: ...
    def get_by_event_id(self, event_id: str) -> SyncEnvelope | None: ...
    def list_enqueued(
        self, *, tenant_id: str, target_device_id: str, limit: int, now: datetime
    ) -> list[SyncEnvelope]:
        """返回目标设备的 ENQUEUED 信封（未过期），按 created_at 升序。"""
        ...

    def mark_acked(self, envelope_id: str, now: datetime) -> SyncEnvelope | None:
        """把信封置为 ACKED；已 ACKED 时返回当前投影（幂等），不存在返回 None。"""
        ...

    def delete_expired(self, now: datetime) -> int:
        """删除 TTL 过期（``expires_at <= now``）的信封，返回删除条数。"""
        ...
