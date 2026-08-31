"""0003_license_product_feature：行业类型、许可证与功能授权（云端迁移第 3 步）。

- ``product_profile``：行业类型（零售/批发/制造等），决定默认配置与能力集；
- ``license``：商户许可证（套餐、起止时间、设备数上限），
  **同一商户只能有一个 ACTIVE**（以部分唯一索引 ``uq_license_tenant_active`` 强制）；
- ``feature_grant``：功能开关授权，``(tenant_id, feature_code)`` 唯一。
- 补齐 0002 遗留的 ``tenant.product_profile_id -> product_profile`` 外键
  （主基线 §35.7 迁移规则：先加字段和兼容代码，再切换读写，最后清理旧字段）。
- 离线宽限期不落表：宽限天数属运行配置（``LICENSE_OFFLINE_GRACE_DAYS``），
  由 ``app.domain.license`` 按到期日 + 宽限天数推导，避免许可证表与配置双写。
- downgrade 完整可逆（先解外键再 drop），空库可重复执行。

依据主基线 §35.6「许可证和能力组」；许可证状态机见 §32 与 domain/license.py。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "control_0003"
down_revision: str | None = "control_0002"
branch_labels: str | None = None
depends_on: str | None = None

#: 行业类型状态
PRODUCT_PROFILE_STATUS_VALUES: tuple[str, ...] = ("ACTIVE", "RETIRED")

#: 许可证状态（domain/license.py）
LICENSE_STATUS_VALUES: tuple[str, ...] = ("ACTIVE", "EXPIRED", "REVOKED")

#: 本迁移新增的控制库枚举种子：kind -> 枚举值集合
SEED_ENUMS: dict[str, tuple[str, ...]] = {
    "product_profile_status": PRODUCT_PROFILE_STATUS_VALUES,
    "license_status": LICENSE_STATUS_VALUES,
}


def upgrade() -> None:
    """建 product_profile / license / feature_grant，补 tenant 外键与枚举种子。"""
    op.create_table(
        "product_profile",
        sa.Column("product_profile_id", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("default_config_version", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("product_profile_id", name="pk_product_profile"),
        sa.UniqueConstraint("code", name="uq_product_profile_code"),
        sa.CheckConstraint(
            f"status IN {_sql_tuple(PRODUCT_PROFILE_STATUS_VALUES)}",
            name="ck_product_profile_status",
        ),
    )

    op.create_table(
        "license",
        sa.Column("license_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("product_profile_id", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.Date(), nullable=False),
        sa.Column("expires_at", sa.Date(), nullable=False),
        sa.Column("max_devices", sa.Integer(), nullable=False, server_default="1"),
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
        sa.PrimaryKeyConstraint("license_id", name="pk_license"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.tenant_id"],
            name="fk_license_tenant_id_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_profile_id"],
            ["product_profile.product_profile_id"],
            name="fk_license_product_profile_id_product_profile",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"status IN {_sql_tuple(LICENSE_STATUS_VALUES)}",
            name="ck_license_status",
        ),
        sa.CheckConstraint("max_devices > 0", name="ck_license_max_devices_positive"),
        sa.CheckConstraint("starts_at <= expires_at", name="ck_license_period_order"),
    )
    # 同一商户只能有一个 ACTIVE 许可证（部分唯一索引；历史 EXPIRED/REVOKED 记录可并存）
    op.create_index(
        "uq_license_tenant_active",
        "license",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    op.create_table(
        "feature_grant",
        sa.Column("feature_grant_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("feature_code", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.Text(), nullable=False, server_default="LICENSE"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("feature_grant_id", name="pk_feature_grant"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.tenant_id"],
            name="fk_feature_grant_tenant_id_tenant",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "feature_code",
            name="uq_feature_grant_tenant_id_feature_code",
        ),
    )

    # 0002 先把 product_profile_id 建成普通列，此处补外键（product_profile 已存在）
    op.create_foreign_key(
        "fk_tenant_product_profile_id_product_profile",
        "tenant",
        "product_profile",
        ["product_profile_id"],
        ["product_profile_id"],
        ondelete="SET NULL",
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
        "UPDATE control_meta SET value = 'control-0003', updated_at = now() "
        "WHERE key = 'db_schema_version'"
    )


def downgrade() -> None:
    """完整回滚：清理枚举种子 → 解 tenant 外键 → drop 三表 → 版本回 control-0002。"""
    for kind, values in SEED_ENUMS.items():
        op.execute(
            f"DELETE FROM control_enum WHERE kind = '{kind}' "
            f"AND code IN {_sql_tuple([f'{kind}:{value}' for value in values])}"
        )
    op.drop_constraint(
        "fk_tenant_product_profile_id_product_profile", "tenant", type_="foreignkey"
    )
    op.drop_table("feature_grant")
    op.drop_index("uq_license_tenant_active", table_name="license")
    op.drop_table("license")
    op.drop_table("product_profile")
    op.execute(
        "UPDATE control_meta SET value = 'control-0002', updated_at = now() "
        "WHERE key = 'db_schema_version'"
    )


def _sql_tuple(values: list[str] | tuple[str, ...]) -> str:
    """把枚举值集合渲染为 SQL IN 列表字面量（值均为受控英文标识）。"""
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"({rendered})"
