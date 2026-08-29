"""0003_master_data：主数据组 7 表（主基线 §35.4 主数据组）。

建表（字段与约束严格对齐 §35.4）：
- sku：sku_id UNIQUE；category / is_active 索引；
- barcode：barcode UNIQUE；FK→sku.sku_id（条码只映射一个有效 SKU 由
  唯一约束 + is_active 过滤在 Repository 层共同保证）；
- warehouse：warehouse_id UNIQUE；
- location：(warehouse_id, location_id) UNIQUE；
- supplier：supplier_id UNIQUE；
- supplier_sku：(supplier_id, sku_id) 组合主键；lead_time_days / moq /
  pack_size / order_cost / holding_cost 非负 CHECK；
- lot：(sku_id, lot_id) UNIQUE；expiry_date >= production_date CHECK
  （ISO 日期文本字典序即时间序；任一为 NULL 时比较结果为 NULL，CHECK 放行）。

M0 约定：金额/数量列用 TEXT 存 Decimal 序列化字符串（禁止 float 真值），
因此非负 CHECK 用 ``CAST(x AS REAL)`` 转数值比较（NULL 转换后仍为 NULL 放行）；
时间列存 UTC ISO 8601 文本、日期列存 YYYY-MM-DD 文本；
每表附 created_at / updated_at（§35.3）。

回滚：反序 drop 七表，db_schema_version 回退 local-0002。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_master_data"
down_revision: str | None = "0002_analysis_m0"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

#: 本地库 schema 版本（与 revision 同步推进）
SCHEMA_VERSION = "local-0003"

_local_meta = sa.table(
    "local_meta",
    sa.column("key", sa.Text),
    sa.column("value", sa.Text),
)


def upgrade() -> None:
    op.create_table(
        "sku",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("sub_category", sa.Text(), nullable=True),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("unit_scale", sa.Text(), nullable=True),
        sa.Column("unit_cost", sa.Text(), nullable=True),
        sa.Column("industry", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sku"),
        sa.UniqueConstraint("sku_id", name="uq_sku_sku_id"),
    )
    op.create_index("ix_sku_category", "sku", ["category"], unique=False)
    op.create_index("ix_sku_is_active", "sku", ["is_active"], unique=False)

    op.create_table(
        "barcode",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("barcode", sa.Text(), nullable=False),
        sa.Column("sku_id", sa.Text(), nullable=False),
        sa.Column("package_unit", sa.Text(), nullable=True),
        sa.Column("conversion_factor", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_barcode"),
        sa.UniqueConstraint("barcode", name="uq_barcode_barcode"),
        sa.ForeignKeyConstraint(
            ["sku_id"],
            ["sku.sku_id"],
            name="fk_barcode_sku_id_sku",
        ),
    )

    op.create_table(
        "warehouse",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_warehouse"),
        sa.UniqueConstraint("warehouse_id", name="uq_warehouse_warehouse_id"),
    )

    op.create_table(
        "location",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Text(), nullable=False),
        sa.Column("location_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_location"),
        sa.UniqueConstraint(
            "warehouse_id", "location_id", name="uq_location_warehouse_id_location_id"
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouse.warehouse_id"],
            name="fk_location_warehouse_id_warehouse",
        ),
    )

    op.create_table(
        "supplier",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("contact", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_supplier"),
        sa.UniqueConstraint("supplier_id", name="uq_supplier_supplier_id"),
    )

    op.create_table(
        "supplier_sku",
        sa.Column("supplier_id", sa.Text(), nullable=False),
        sa.Column("sku_id", sa.Text(), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=True),
        sa.Column("moq", sa.Text(), nullable=True),
        sa.Column("pack_size", sa.Text(), nullable=True),
        sa.Column("order_cost", sa.Text(), nullable=True),
        sa.Column("holding_cost", sa.Text(), nullable=True),
        sa.Column("is_preferred", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("supplier_id", "sku_id", name="pk_supplier_sku"),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["supplier.supplier_id"],
            name="fk_supplier_sku_supplier_id_supplier",
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"],
            ["sku.sku_id"],
            name="fk_supplier_sku_sku_id_sku",
        ),
        sa.CheckConstraint(
            "lead_time_days >= 0", name="ck_supplier_sku_lead_time_nonneg"
        ),
        # Decimal 存 TEXT，先 CAST 成数值再做非负比较（NULL 参数放行）
        sa.CheckConstraint("CAST(moq AS REAL) >= 0", name="ck_supplier_sku_moq_nonneg"),
        sa.CheckConstraint(
            "CAST(pack_size AS REAL) >= 0", name="ck_supplier_sku_pack_size_nonneg"
        ),
        sa.CheckConstraint(
            "CAST(order_cost AS REAL) >= 0", name="ck_supplier_sku_order_cost_nonneg"
        ),
        sa.CheckConstraint(
            "CAST(holding_cost AS REAL) >= 0", name="ck_supplier_sku_holding_cost_nonneg"
        ),
    )

    op.create_table(
        "lot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lot_id", sa.Text(), nullable=False),
        sa.Column("sku_id", sa.Text(), nullable=False),
        sa.Column("production_date", sa.Text(), nullable=True),
        sa.Column("expiry_date", sa.Text(), nullable=True),
        sa.Column("received_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_lot"),
        sa.UniqueConstraint("sku_id", "lot_id", name="uq_lot_sku_id_lot_id"),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.sku_id"], name="fk_lot_sku_id_sku"),
        # ISO 日期文本字典序即时间序；有效期不得早于生产日期
        sa.CheckConstraint(
            "expiry_date >= production_date",
            name="ck_lot_expiry_not_before_production",
        ),
    )

    op.execute(
        sa.update(_local_meta)
        .where(_local_meta.c.key == "db_schema_version")
        .values(value=SCHEMA_VERSION)
    )


def downgrade() -> None:
    op.execute(
        sa.update(_local_meta)
        .where(_local_meta.c.key == "db_schema_version")
        .values(value="local-0002")
    )
    # 反序回滚：先删引用表，再删被引用表
    op.drop_table("lot")
    op.drop_table("supplier_sku")
    op.drop_table("supplier")
    op.drop_table("location")
    op.drop_table("warehouse")
    op.drop_table("barcode")
    op.drop_index("ix_sku_is_active", table_name="sku")
    op.drop_index("ix_sku_category", table_name="sku")
    op.drop_table("sku")
