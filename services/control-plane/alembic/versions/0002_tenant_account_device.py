"""0002_tenant_account_device：租户、账号、会话与设备（云端迁移第 2 步）。

- ``tenant``：商户；``account``：商户主账号与开发者账号；``session``：登录会话与
  Refresh Token 指纹；``device``：工作台/Web/小程序设备。
- 表与字段依据主基线 §35.6「租户、账号和设备组」，状态取值对齐 §32.3（设备）
  与 domain 层状态机（账号）。
- 密码只存 Argon2id 哈希（``account.password_hash``），Refresh Token 只存 SHA-256
  指纹（``session.refresh_token_hash``），均不保存明文（§35.5）。
- ``session`` 采用「一次登录一行」模型：轮换时覆盖 ``refresh_token_hash`` 并把旧值
  移入 ``previous_refresh_token_hash`` 用于重放检测；撤销写 ``revoked_at``（终态）。
- ``tenant.product_profile_id`` 在 0003 建 ``product_profile`` 后补外键（先加字段后加约束）。
- 全部状态列加 CHECK；未知枚举不能静默落库（§35.5）。
- downgrade 完整可逆（按外键逆序 drop），空库可重复执行。

依据主基线 §35.7：本步只建租户、账号、会话与设备，不建立任何商户业务明细表。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "control_0002"
down_revision: str | None = "control_0001"
branch_labels: str | None = None
depends_on: str | None = None

#: 商户状态（domain/tenant.py）
TENANT_STATUS_VALUES: tuple[str, ...] = ("ACTIVE", "SUSPENDED")

#: 账号状态（domain/account.py）
ACCOUNT_STATUS_VALUES: tuple[str, ...] = ("ACTIVE", "LOCKED", "DISABLED")

#: 账号角色（主基线 §35.6：至少 MERCHANT_OWNER / DEVELOPER）
ACCOUNT_ROLE_VALUES: tuple[str, ...] = ("MERCHANT_OWNER", "DEVELOPER")

#: 会话客户端类型（主基线 §7.2：工作台、Web 会话和小程序设备）
CLIENT_TYPE_VALUES: tuple[str, ...] = ("DESKTOP", "WEB", "MINI_PROGRAM")

#: 设备状态（主基线 §32.3）
DEVICE_STATUS_VALUES: tuple[str, ...] = (
    "REGISTERED",
    "ONLINE",
    "DEGRADED",
    "OFFLINE",
    "REVOKED",
)

#: 本迁移新增的控制库枚举种子：kind -> 枚举值集合
SEED_ENUMS: dict[str, tuple[str, ...]] = {
    "tenant_status": TENANT_STATUS_VALUES,
    "account_status": ACCOUNT_STATUS_VALUES,
    "account_role": ACCOUNT_ROLE_VALUES,
    "client_type": CLIENT_TYPE_VALUES,
    "device_status": DEVICE_STATUS_VALUES,
}

def upgrade() -> None:
    """建 tenant / account / session / device 四表、约束、索引与枚举种子。"""
    op.create_table(
        "tenant",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("product_profile_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="ACTIVE"),
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
        sa.PrimaryKeyConstraint("tenant_id", name="pk_tenant"),
        sa.CheckConstraint(
            f"status IN {_sql_tuple(TENANT_STATUS_VALUES)}",
            name="ck_tenant_status",
        ),
    )

    op.create_table(
        "account",
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=True),
        sa.Column("login_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="ACTIVE"),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("account_id", name="pk_account"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.tenant_id"],
            name="fk_account_tenant_id_tenant",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("login_name", name="uq_account_login_name"),
        sa.CheckConstraint(
            f"role IN {_sql_tuple(ACCOUNT_ROLE_VALUES)}",
            name="ck_account_role",
        ),
        sa.CheckConstraint(
            f"status IN {_sql_tuple(ACCOUNT_STATUS_VALUES)}",
            name="ck_account_status",
        ),
        sa.CheckConstraint(
            "failed_attempts >= 0",
            name="ck_account_failed_attempts_non_negative",
        ),
        # 开发者账号不属于任何商户；商户账号必须绑定租户，禁止出现"无租户的商户账号"
        sa.CheckConstraint(
            "role = 'DEVELOPER' OR tenant_id IS NOT NULL",
            name="ck_account_tenant_required_for_merchant",
        ),
    )
    op.create_index("ix_account_tenant_id", "account", ["tenant_id"])

    op.create_table(
        "session",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("client_type", sa.Text(), nullable=False),
        sa.Column("device_id", sa.Text(), nullable=True),
        sa.Column("refresh_token_hash", sa.Text(), nullable=False),
        sa.Column("previous_refresh_token_hash", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("session_id", name="pk_session"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.account_id"],
            name="fk_session_account_id_account",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("refresh_token_hash", name="uq_session_refresh_token_hash"),
        sa.CheckConstraint(
            f"client_type IN {_sql_tuple(CLIENT_TYPE_VALUES)}",
            name="ck_session_client_type",
        ),
    )
    op.create_index("ix_session_account_id", "session", ["account_id"])
    op.create_index("ix_session_expires_at", "session", ["expires_at"])

    op.create_table(
        "device",
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("device_type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="REGISTERED"),
        sa.Column("app_version", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "registered_at",
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
        sa.PrimaryKeyConstraint("device_id", name="pk_device"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.tenant_id"],
            name="fk_device_tenant_id_tenant",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "fingerprint",
            name="uq_device_tenant_id_fingerprint",
        ),
        sa.CheckConstraint(
            f"device_type IN {_sql_tuple(CLIENT_TYPE_VALUES)}",
            name="ck_device_device_type",
        ),
        sa.CheckConstraint(
            f"status IN {_sql_tuple(DEVICE_STATUS_VALUES)}",
            name="ck_device_status",
        ),
    )
    op.create_index("ix_device_tenant_id_status", "device", ["tenant_id", "status"])

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


def downgrade() -> None:
    """完整回滚：清理枚举种子后按外键逆序 drop 四表。"""
    for kind, values in SEED_ENUMS.items():
        op.execute(
            f"DELETE FROM control_enum WHERE kind = '{kind}' "
            f"AND code IN {_sql_tuple([f'{kind}:{value}' for value in values])}"
        )
    op.drop_table("device")
    op.drop_table("session")
    op.drop_table("account")
    op.drop_table("tenant")


def _sql_tuple(values: list[str] | tuple[str, ...]) -> str:
    """把枚举值集合渲染为 SQL IN 列表字面量（值均为受控英文标识）。"""
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"({rendered})"
