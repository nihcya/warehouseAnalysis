"""0004_inventory_events：业务事件组 6 表（主基线 §35.4 业务事件组）。

建表（字段与约束严格对齐 §35.4）：
- inventory_event：event_id UNIQUE；quantity > 0 CHECK；
  (sku_id, warehouse_id, occurred_at) 索引；FK→sku/warehouse；
  location_id / lot_id 单列不唯一（唯一性在主数据组合键上），故分别与
  warehouse_id / sku_id 组成复合外键引用 location / lot；
  reversal_of 自引用外键（冲销指向原事件）；
- inventory_event_line：(event_id, line_no) UNIQUE；FK→inventory_event / sku；
- stock_snapshot：(snapshot_date, sku_id, warehouse_id, location_id, lot_id)
  UNIQUE；FK 同事件表口径；
- purchase_order：po_id UNIQUE；status 枚举 CHECK
  （DRAFT/ORDERED/RECEIVED/CLOSED/CANCELLED）；FK→supplier；
- purchase_order_line：(po_id, line_no) UNIQUE；ordered/received 非负 CHECK
  （超收容差属 replenishment 业务口径，M1 只做非负下界）；
- inventory_balance：事件投影表（非唯一事实来源）。身份维度
  (sku_id, warehouse_id, location_id, lot_id) 不设业务 UNIQUE——SQLite 组合
  唯一对 NULL 维度不生效，重建口径为"清空后按事件回放写入"；
  as_of_event_id 记录该维度最后回放到的事件。

M0 约定沿用：金额/数量 TEXT 存 Decimal 字符串，CHECK 用 CAST 数值比较；
时间 UTC ISO 8601 文本、日期 YYYY-MM-DD 文本；每表 created_at/updated_at。

回滚：反序 drop 六表，db_schema_version 回退 local-0003。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_inventory_events"
down_revision: str | None = "0003_master_data"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

#: 本地库 schema 版本（与 revision 同步推进）
SCHEMA_VERSION = "local-0004"

_local_meta = sa.table(
    "local_meta",
    sa.column("key", sa.Text),
    sa.column("value", sa.Text),
)


def upgrade() -> None:
    op.create_table(
        "inventory_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("sku_id", sa.Text(), nullable=False),
        sa.Column("warehouse_id", sa.Text(), nullable=False),
        sa.Column("location_id", sa.Text(), nullable=True),
        sa.Column("lot_id", sa.Text(), nullable=True),
        sa.Column("move_type", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Text(), nullable=False),
        sa.Column("unit_cost", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("reversal_of", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_inventory_event"),
        sa.UniqueConstraint("event_id", name="uq_inventory_event_event_id"),
        sa.ForeignKeyConstraint(
            ["sku_id"], ["sku.sku_id"], name="fk_inventory_event_sku_id_sku"
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouse.warehouse_id"],
            name="fk_inventory_event_warehouse_id_warehouse",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id", "location_id"],
            ["location.warehouse_id", "location.location_id"],
            name="fk_inventory_event_warehouse_id_location",
        ),
        sa.ForeignKeyConstraint(
            ["sku_id", "lot_id"],
            ["lot.sku_id", "lot.lot_id"],
            name="fk_inventory_event_sku_id_lot",
        ),
        sa.ForeignKeyConstraint(
            ["reversal_of"],
            ["inventory_event.event_id"],
            name="fk_inventory_event_reversal_of_inventory_event",
        ),
        # Decimal 存 TEXT，CAST 成数值比较；数量必须大于 0（方向由 move_type 承载）
        sa.CheckConstraint(
            "CAST(quantity AS REAL) > 0", name="ck_inventory_event_quantity_positive"
        ),
    )
    op.create_index(
        "ix_inventory_event_sku_id_warehouse_id_occurred_at",
        "inventory_event",
        ["sku_id", "warehouse_id", "occurred_at"],
        unique=False,
    )

    op.create_table(
        "inventory_event_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Text(), nullable=False),
        sa.Column("unit_cost", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_inventory_event_line"),
        sa.UniqueConstraint(
            "event_id", "line_no", name="uq_inventory_event_line_event_id_line_no"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["inventory_event.event_id"],
            name="fk_inventory_event_line_event_id_inventory_event",
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"], ["sku.sku_id"], name="fk_inventory_event_line_sku_id_sku"
        ),
    )

    op.create_table(
        "stock_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("snapshot_date", sa.Text(), nullable=False),
        sa.Column("sku_id", sa.Text(), nullable=False),
        sa.Column("warehouse_id", sa.Text(), nullable=False),
        sa.Column("location_id", sa.Text(), nullable=True),
        sa.Column("lot_id", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Text(), nullable=False),
        sa.Column("inventory_value", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_stock_snapshot"),
        sa.UniqueConstraint("snapshot_id", name="uq_stock_snapshot_snapshot_id"),
        sa.UniqueConstraint(
            "snapshot_date",
            "sku_id",
            "warehouse_id",
            "location_id",
            "lot_id",
            name="uq_stock_snapshot_date_sku_wh_loc_lot",
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"], ["sku.sku_id"], name="fk_stock_snapshot_sku_id_sku"
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouse.warehouse_id"],
            name="fk_stock_snapshot_warehouse_id_warehouse",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id", "location_id"],
            ["location.warehouse_id", "location.location_id"],
            name="fk_stock_snapshot_warehouse_id_location",
        ),
        sa.ForeignKeyConstraint(
            ["sku_id", "lot_id"],
            ["lot.sku_id", "lot.lot_id"],
            name="fk_stock_snapshot_sku_id_lot",
        ),
    )

    op.create_table(
        "purchase_order",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("po_id", sa.Text(), nullable=False),
        sa.Column("supplier_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("ordered_at", sa.Text(), nullable=True),
        sa.Column("expected_at", sa.Text(), nullable=True),
        sa.Column("closed_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_purchase_order"),
        sa.UniqueConstraint("po_id", name="uq_purchase_order_po_id"),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["supplier.supplier_id"],
            name="fk_purchase_order_supplier_id_supplier",
        ),
        # 状态枚举：未知状态不允许落库（业务语义随 replenishment 落地，M1 先约束取值域）
        sa.CheckConstraint(
            "status IN ('DRAFT', 'ORDERED', 'RECEIVED', 'CLOSED', 'CANCELLED')",
            name="ck_purchase_order_status",
        ),
    )

    op.create_table(
        "purchase_order_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("po_id", sa.Text(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Text(), nullable=False),
        sa.Column("ordered_qty", sa.Text(), nullable=False),
        sa.Column("received_qty", sa.Text(), nullable=False),
        sa.Column("unit_cost", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_purchase_order_line"),
        sa.UniqueConstraint("po_id", "line_no", name="uq_purchase_order_line_po_id_line_no"),
        sa.ForeignKeyConstraint(
            ["po_id"],
            ["purchase_order.po_id"],
            name="fk_purchase_order_line_po_id_purchase_order",
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"], ["sku.sku_id"], name="fk_purchase_order_line_sku_id_sku"
        ),
        sa.CheckConstraint(
            "CAST(ordered_qty AS REAL) >= 0",
            name="ck_purchase_order_line_ordered_qty_nonneg",
        ),
        sa.CheckConstraint(
            "CAST(received_qty AS REAL) >= 0",
            name="ck_purchase_order_line_received_qty_nonneg",
        ),
    )

    op.create_table(
        "inventory_balance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Text(), nullable=False),
        sa.Column("warehouse_id", sa.Text(), nullable=False),
        sa.Column("location_id", sa.Text(), nullable=True),
        sa.Column("lot_id", sa.Text(), nullable=True),
        sa.Column("on_hand_qty", sa.Text(), nullable=False),
        sa.Column("available_qty", sa.Text(), nullable=False),
        sa.Column("reserved_qty", sa.Text(), nullable=False),
        sa.Column("as_of_event_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_inventory_balance"),
        sa.ForeignKeyConstraint(
            ["sku_id"], ["sku.sku_id"], name="fk_inventory_balance_sku_id_sku"
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouse.warehouse_id"],
            name="fk_inventory_balance_warehouse_id_warehouse",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id", "location_id"],
            ["location.warehouse_id", "location.location_id"],
            name="fk_inventory_balance_warehouse_id_location",
        ),
        sa.ForeignKeyConstraint(
            ["sku_id", "lot_id"],
            ["lot.sku_id", "lot.lot_id"],
            name="fk_inventory_balance_sku_id_lot",
        ),
        sa.ForeignKeyConstraint(
            ["as_of_event_id"],
            ["inventory_event.event_id"],
            name="fk_inventory_balance_as_of_event_id_inventory_event",
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
        .values(value="local-0003")
    )
    # 反序回滚：投影 → 采购 → 快照 → 事件
    op.drop_table("inventory_balance")
    op.drop_table("purchase_order_line")
    op.drop_table("purchase_order")
    op.drop_table("stock_snapshot")
    op.drop_table("inventory_event_line")
    op.drop_index(
        "ix_inventory_event_sku_id_warehouse_id_occurred_at",
        table_name="inventory_event",
    )
    op.drop_table("inventory_event")
