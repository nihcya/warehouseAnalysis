"""云端控制库 ORM 模型（SQLAlchemy 2.0 declarative，M2）。

- 表结构对齐主基线 §35.6，DDL 一律由 Alembic 迁移创建
  （0002_tenant_account_device、0003_license_product_feature、0004_audit）；
  本文件与本地库一样只声明列/外键/唯一/索引，迁移才是 DDL 唯一权威。
- 时间字段统一 ``TIMESTAMPTZ`` 存 UTC（主基线 §35.5），日期用 ``DATE``；
- 状态与角色列以 ``Text`` + 迁移内 CHECK 约束存储：读取时经
  ``app.infrastructure.db.repositories`` 转成 domain 枚举，
  未知取值直接抛错，不静默降级（§35.5：未知枚举不能静默落库）。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: 云端控制库约束命名约定（与迁移内显式约束名保持一致）
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """云端库 declarative 基类（metadata 携带命名约定）。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TenantRow(Base):
    """tenant：商户。"""

    __tablename__ = "tenant"

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    product_profile_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("product_profile.product_profile_id"), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AccountRow(Base):
    """account：商户主账号与开发者账号。"""

    __tablename__ = "account"

    account_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("tenant.tenant_id"), nullable=True
    )
    login_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SessionRow(Base):
    """session：登录会话与 Refresh Token 指纹（一次登录一行，轮换覆盖指纹）。"""

    __tablename__ = "session"

    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[str] = mapped_column(
        Text, ForeignKey("account.account_id"), nullable=False
    )
    client_type: Mapped[str] = mapped_column(Text, nullable=False)
    device_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    previous_refresh_token_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeviceRow(Base):
    """device：工作台 / Web / 小程序设备。"""

    __tablename__ = "device"

    device_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, ForeignKey("tenant.tenant_id"), nullable=False)
    device_type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    app_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProductProfileRow(Base):
    """product_profile：行业类型。"""

    __tablename__ = "product_profile"

    product_profile_id: Mapped[str] = mapped_column(Text, primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    default_config_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LicenseRow(Base):
    """license：商户许可证（同一商户只能有一个 ACTIVE）。"""

    __tablename__ = "license"

    license_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, ForeignKey("tenant.tenant_id"), nullable=False)
    product_profile_id: Mapped[str] = mapped_column(
        Text, ForeignKey("product_profile.product_profile_id"), nullable=False
    )
    #: 许可证生效/到期为「自然日」语义（迁移中列为 sa.Date），领域模型亦为 date。
    #: 曾误注解为 Mapped[datetime]，导致读取时调用 .date() 抛 AttributeError（Issue #18）。
    starts_at: Mapped[date] = mapped_column(Date, nullable=False)
    expires_at: Mapped[date] = mapped_column(Date, nullable=False)
    max_devices: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FeatureGrantRow(Base):
    """feature_grant：商户功能开关授权。"""

    __tablename__ = "feature_grant"

    feature_grant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, ForeignKey("tenant.tenant_id"), nullable=False)
    feature_code: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLogRow(Base):
    """audit_log：审计记录（只追加，不提供更新入口）。"""

    __tablename__ = "audit_log"

    audit_id: Mapped[str] = mapped_column(Text, primary_key=True)
    actor_account_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConfigVersionRow(Base):
    """config_version：商户配置版本（M3，版本不可覆盖 + 发布状态机）。"""

    __tablename__ = "config_version"

    config_version_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        Text, ForeignKey("tenant.tenant_id"), nullable=False
    )
    version: Mapped[str] = mapped_column(Text, nullable=False)
    content_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    published_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskRow(Base):
    """task：调度任务（云端定义，本地执行）。"""

    __tablename__ = "task"

    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        Text, ForeignKey("tenant.tenant_id"), nullable=False
    )
    task_type: Mapped[str] = mapped_column(Text, nullable=False)
    cron_expr: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskRunRow(Base):
    """task_run：任务运行投影（只存状态与摘要，不存业务明细原文）。"""

    __tablename__ = "task_run"

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, ForeignKey("task.task_id"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(
        Text, ForeignKey("tenant.tenant_id"), nullable=False
    )
    device_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HeartbeatRow(Base):
    """heartbeat：设备心跳最新投影（device_id 主键，一行一台设备）。"""

    __tablename__ = "heartbeat"

    device_id: Mapped[str] = mapped_column(
        Text, ForeignKey("device.device_id"), primary_key=True
    )
    tenant_id: Mapped[str] = mapped_column(
        Text, ForeignKey("tenant.tenant_id"), nullable=False
    )
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    app_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    engine_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    db_schema_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_sync_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SyncEnvelopeRow(Base):
    """sync_envelope：小程序事件加密信封（event_id 全局唯一，密文中继）。"""

    __tablename__ = "sync_envelope"

    envelope_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        Text, ForeignKey("tenant.tenant_id"), nullable=False
    )
    target_device_id: Mapped[str] = mapped_column(
        Text, ForeignKey("device.device_id"), nullable=False
    )
    event_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
