"""0004_audit：审计日志（cloud 控制库）。

迁移顺序说明（偏离主基线 §35.7 的 0006_sync_telemetry_audit）：
M2 第 4 项要求"所有管理操作接入 Scope、审计、限时支持授权和操作确认"，
而 M2 本批交付的登录、刷新、注销、设备注册与许可证变更都是必须留痕的高风险动作，
故把 ``audit_log`` 从 §35.7 规划的 0006 提前到 0004 单独成表；
0006 后续只承载 ``sync_envelope`` 与 ``telemetry_event``，不重复建 audit_log。

- 审计只记录动作与结果摘要（actor、租户、动作、目标、request_id、结果），
  **不保存密码、令牌、业务明细原文**（主基线 §35.10、SECURITY.md）。
- ``detail_json`` 由应用层按字段白名单写入（如失败原因码、设备类型），
  数据库层不加内容约束，白名单在 ``app/application/audit.py`` 统一收敛。
- ``actor_account_id`` 对"登录失败"这类无账号场景允许为空，故不用外键强约束；
  ``tenant_id`` 同理（开发者动作可不归属商户）。
- 保留周期：安全审计日志默认 180 天（§35.10），清理任务随运维里程碑落地。

downgrade 完整可逆（drop 表 + 回写版本号），空库可重复执行。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "control_0004"
down_revision: str | None = "control_0003"
branch_labels: str | None = None
depends_on: str | None = None

#: 审计动作字典（应用层写入前校验；新增动作走"只加不改"）
AUDIT_ACTION_VALUES: tuple[str, ...] = (
    "AUTH_LOGIN",
    "AUTH_REFRESH",
    "AUTH_LOGOUT",
    "DEVICE_REGISTER",
    "LICENSE_STATUS_CHANGE",
)

#: 审计结果
AUDIT_RESULT_VALUES: tuple[str, ...] = ("SUCCESS", "DENIED", "ERROR")

#: 本迁移新增的控制库枚举种子：kind -> 枚举值集合
SEED_ENUMS: dict[str, tuple[str, ...]] = {
    "audit_action": AUDIT_ACTION_VALUES,
    "audit_result": AUDIT_RESULT_VALUES,
}


def upgrade() -> None:
    """建 audit_log 表、索引与枚举种子，并把 db_schema_version 升到 control-0004。"""
    op.create_table(
        "audit_log",
        sa.Column("audit_id", sa.Text(), nullable=False),
        sa.Column("actor_account_id", sa.Text(), nullable=True),
        sa.Column("actor_role", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("detail_json", sa.JSON(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("audit_id", name="pk_audit_log"),
        sa.CheckConstraint(
            f"action IN {_sql_tuple(AUDIT_ACTION_VALUES)}",
            name="ck_audit_log_action",
        ),
        sa.CheckConstraint(
            f"result IN {_sql_tuple(AUDIT_RESULT_VALUES)}",
            name="ck_audit_log_result",
        ),
    )
    op.create_index("ix_audit_log_tenant_occurred", "audit_log", ["tenant_id", "occurred_at"])
    op.create_index("ix_audit_log_actor_occurred", "audit_log", ["actor_account_id", "occurred_at"])

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
        "UPDATE control_meta SET value = 'control-0004', updated_at = now() "
        "WHERE key = 'db_schema_version'"
    )


def downgrade() -> None:
    """完整回滚：清理枚举种子 → drop audit_log → 版本回 control-0003。"""
    for kind, values in SEED_ENUMS.items():
        op.execute(
            f"DELETE FROM control_enum WHERE kind = '{kind}' "
            f"AND code IN {_sql_tuple([f'{kind}:{value}' for value in values])}"
        )
    op.drop_index("ix_audit_log_actor_occurred", table_name="audit_log")
    op.drop_index("ix_audit_log_tenant_occurred", table_name="audit_log")
    op.drop_table("audit_log")
    op.execute(
        "UPDATE control_meta SET value = 'control-0003', updated_at = now() "
        "WHERE key = 'db_schema_version'"
    )


def _sql_tuple(values: list[str] | tuple[str, ...]) -> str:
    """把枚举值集合渲染为 SQL IN 列表字面量（值均为受控英文标识）。"""
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"({rendered})"
