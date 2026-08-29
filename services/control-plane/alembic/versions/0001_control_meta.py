"""0001_control_meta：控制库元数据表与基础枚举（云端迁移第 1 步）。

- ``control_meta``：数据库版本与元信息（种子写入 ``db_schema_version=control-0001``）；
- ``control_enum``：基础枚举字典（task_status / run_status / device_status /
  sync_status / move_type），种子数据以 data migration（bulk_insert）写入；
- ``code`` 采用 ``<kind>:<value>`` 形式以保证全局唯一（不同 kind 的枚举值可能重名，
  如 task_status 与 sync_status 都有 CREATED）；
- 状态取值对齐主基线 §32（关键状态机）；move_type 直接取 contracts.MoveType；
- downgrade 完整可逆（drop 两表），空库可重复执行。

依据主基线 §35.7：M0 只建元数据与枚举，不建立任何商户业务明细表。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from contracts import MoveType

revision: str = "control-0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

#: 分析任务状态（主基线 §32.1）
TASK_STATUS_VALUES: tuple[str, ...] = (
    "CREATED",
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "MISSED",
    "RETRYING",
)

#: 任务运行（task_run）状态（与任务状态机同构，主基线 §32.1 / §35.2.2）
RUN_STATUS_VALUES: tuple[str, ...] = TASK_STATUS_VALUES

#: 设备状态（主基线 §32.3）
DEVICE_STATUS_VALUES: tuple[str, ...] = (
    "REGISTERED",
    "ONLINE",
    "DEGRADED",
    "OFFLINE",
    "REVOKED",
)

#: 同步事件状态（主基线 §32.2）
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

#: 控制库基础枚举种子：kind -> 枚举值集合（move_type 直接取共享契约）
SEED_ENUMS: dict[str, tuple[str, ...]] = {
    "task_status": TASK_STATUS_VALUES,
    "run_status": RUN_STATUS_VALUES,
    "device_status": DEVICE_STATUS_VALUES,
    "sync_status": SYNC_STATUS_VALUES,
    "move_type": tuple(value.value for value in MoveType),
}

#: control_meta 种子元信息
SEED_META: dict[str, str] = {
    "db_schema_version": "control-0001",
    "control_plane_version": "0.1.0",
}


def upgrade() -> None:
    """建 control_meta / control_enum 两表并写入种子数据。"""
    control_meta = op.create_table(
        "control_meta",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
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
        sa.PrimaryKeyConstraint("key"),
    )
    control_enum = op.create_table(
        "control_enum",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.bulk_insert(
        control_meta,
        [{"key": key, "value": value} for key, value in SEED_META.items()],
    )
    op.bulk_insert(
        control_enum,
        [
            {"code": f"{kind}:{value}", "kind": kind}
            for kind, values in SEED_ENUMS.items()
            for value in values
        ],
    )


def downgrade() -> None:
    """完整回滚：drop control_enum 与 control_meta（先子后父，无外键依赖）。"""
    op.drop_table("control_enum")
    op.drop_table("control_meta")
