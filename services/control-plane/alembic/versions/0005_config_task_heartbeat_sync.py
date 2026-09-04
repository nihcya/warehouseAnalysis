"""0005_config_task_heartbeat_sync：配置版本、任务、心跳与同步信封（M3 数据层）。

迁移顺序说明（偏离主基线 §35.7 的规划编号）：
规划的 0004_config_skill_model 中的 config_version、0005_task_heartbeat 中的
task / task_run / heartbeat 与 0006_sync_telemetry_audit 中的 sync_envelope
同属 M3 控制平面数据层交付；因 0004 已被 M2 的 audit_log 占用
（见 0004_audit.py 说明），M3 把本批四组表合并为一个迁移 0005，
telemetry_event 仍留在规划的 0006 及以后。

- ``config_version``：商户配置版本（版本不可覆盖：``(tenant_id, version)`` 唯一；
  发布状态机 DRAFT/PUBLISHED/RETIRED；SHA-256 摘要与签名必填）；
- ``task`` / ``task_run``：调度任务与运行投影（run 状态 CHECK 对齐 0001 的
  run_status 种子，状态机迁移由应用层 domain.task.RUN_TRANSITIONS 守卫）；
- ``heartbeat``：设备心跳最新投影（device_id 主键，一行一台设备；
  status 为 Agent 自报运行状态文本，非冻结状态机，不加 CHECK）；
- ``sync_envelope``：小程序事件加密信封（event_id 全局唯一；status CHECK 对齐
  0001 的 sync_status 种子；TTL 过期由应用层在拉取时清理，spec 的「PENDING」
  语义对应 ENQUEUED）；
- 索引对齐主基线 §35.8：``task_run(device_id, status, scheduled_at)``、
  ``sync_envelope(target_device_id, status, expires_at)``。

downgrade 完整可逆（drop 表 + 清理枚举种子 + 回写版本号），空库可重复执行。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "control_0005"
down_revision: str | None = "control_0004"
branch_labels: str | None = None
depends_on: str | None = None

#: 配置版本状态（domain/config.py）
CONFIG_STATUS_VALUES: tuple[str, ...] = ("DRAFT", "PUBLISHED", "RETIRED")

#: 任务运行状态（与 0001_control_meta 的 run_status 种子一致，主基线 §32.1）
RUN_STATUS_VALUES: tuple[str, ...] = (
    "CREATED",
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "MISSED",
    "RETRYING",
)

#: 同步信封状态（与 0001_control_meta 的 sync_status 种子一致，主基线 §32.2）
SYNC_STATUS_VALUES: tuple[str, ...] = (
    "CREATED",
    "ENQUEUED",
    "DELIVERED",
    "APPLIED",
    "ACKED",
    "EXPIRED",
    "REJECTED",
    "RETRYING",
)

#: 本迁移新增的控制库枚举种子：kind -> 枚举值集合。
#: run_status / sync_status 已由 0001_control_meta 播种，此处不得重复插入
#: （control_enum.code 全局唯一，重复会导致 upgrade 唯一约束冲突）。
SEED_ENUMS: dict[str, tuple[str, ...]] = {
    "config_status": CONFIG_STATUS_VALUES,
}


def upgrade() -> None:
    """建 config_version / task / task_run / heartbeat / sync_envelope 五表与枚举种子。"""
    op.create_table(
        "config_version",
        sa.Column("config_version_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False, server_default="DRAFT"),
        sa.Column("published_by", sa.Text(), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("config_version_id", name="pk_config_version"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.tenant_id"],
            name="fk_config_version_tenant_id_tenant",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "version", name="uq_config_version_tenant_version"),
        sa.CheckConstraint(
            f"status IN {_sql_tuple(CONFIG_STATUS_VALUES)}",
            name="ck_config_version_status",
        ),
        sa.CheckConstraint("length(sha256) = 64", name="ck_config_version_sha256_len"),
    )
    op.create_index(
        "ix_config_version_tenant_status",
        "config_version",
        ["tenant_id", "status"],
    )

    op.create_table(
        "task",
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("task_type", sa.Text(), nullable=False),
        sa.Column("cron_expr", sa.Text(), nullable=True),
        sa.Column("scope_json", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("task_id", name="pk_task"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.tenant_id"],
            name="fk_task_tenant_id_tenant",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "task_id", name="uq_task_tenant_task_id"),
    )
    op.create_index("ix_task_tenant_enabled", "task", ["tenant_id", "enabled"])

    op.create_table(
        "task_run",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("device_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="CREATED"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_task_run"),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.task_id"],
            name="fk_task_run_task_id_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.tenant_id"],
            name="fk_task_run_tenant_id_tenant",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"status IN {_sql_tuple(RUN_STATUS_VALUES)}",
            name="ck_task_run_status",
        ),
    )
    # 主基线 §35.8：云端任务拉取 task_run(device_id, status, scheduled_at)
    op.create_index(
        "ix_task_run_device_status_scheduled",
        "task_run",
        ["device_id", "status", "scheduled_at"],
    )

    op.create_table(
        "heartbeat",
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("app_version", sa.Text(), nullable=True),
        sa.Column("engine_version", sa.Text(), nullable=True),
        sa.Column("db_schema_version", sa.Text(), nullable=True),
        sa.Column(
            "pending_sync_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("device_id", name="pk_heartbeat"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.tenant_id"],
            name="fk_heartbeat_tenant_id_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["device.device_id"],
            name="fk_heartbeat_device_id_device",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "pending_sync_count >= 0",
            name="ck_heartbeat_pending_sync_non_negative",
        ),
    )

    op.create_table(
        "sync_envelope",
        sa.Column("envelope_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("target_device_id", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="ENQUEUED"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("envelope_id", name="pk_sync_envelope"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.tenant_id"],
            name="fk_sync_envelope_tenant_id_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_device_id"],
            ["device.device_id"],
            name="fk_sync_envelope_target_device_id_device",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("event_id", name="uq_sync_envelope_event_id"),
        sa.CheckConstraint(
            f"status IN {_sql_tuple(SYNC_STATUS_VALUES)}",
            name="ck_sync_envelope_status",
        ),
    )
    # 主基线 §35.8：云端同步中继 sync_envelope(target_device_id, status, expires_at)
    op.create_index(
        "ix_sync_envelope_device_status_expires",
        "sync_envelope",
        ["target_device_id", "status", "expires_at"],
    )

    enum_table = sa.table(
        "control_enum",
        sa.column("code", sa.Text),
        sa.column("kind", sa.Text),
    )
    op.bulk_insert(
        enum_table,
        [
            {"code": f"{kind}:{value}", "kind": kind}
            for kind, values in SEED_ENUMS.items()
            for value in values
        ],
    )

    op.execute(
        "UPDATE control_meta SET value = 'control-0005', updated_at = now() "
        "WHERE key = 'db_schema_version'"
    )


def downgrade() -> None:
    """完整回滚：清理枚举种子 → 按外键逆序 drop 五表 → 版本回 control-0004。"""
    for kind, values in SEED_ENUMS.items():
        op.execute(
            f"DELETE FROM control_enum WHERE kind = '{kind}' "
            f"AND code IN {_sql_tuple([f'{kind}:{value}' for value in values])}"
        )
    op.drop_index("ix_sync_envelope_device_status_expires", table_name="sync_envelope")
    op.drop_table("sync_envelope")
    op.drop_table("heartbeat")
    op.drop_index("ix_task_run_device_status_scheduled", table_name="task_run")
    op.drop_table("task_run")
    op.drop_index("ix_task_tenant_enabled", table_name="task")
    op.drop_table("task")
    op.drop_index("ix_config_version_tenant_status", table_name="config_version")
    op.drop_table("config_version")
    op.execute(
        "UPDATE control_meta SET value = 'control-0004', updated_at = now() "
        "WHERE key = 'db_schema_version'"
    )


def _sql_tuple(values: list[str] | tuple[str, ...]) -> str:
    """把枚举值集合渲染为 SQL IN 列表字面量（值均为受控英文标识）。"""
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"({rendered})"
