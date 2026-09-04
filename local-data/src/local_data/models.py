"""本地业务库 ORM 模型（SQLAlchemy 2.0 declarative）。

- 表结构对齐主基线 §35.4；DDL 一律由 Alembic 迁移创建
  （0001_meta、0002_analysis_m0、0003_master_data、0004_inventory_events、
  0005_import、0006_report_backup、0007_sync_config）。
- 时间字段统一存 UTC ISO 8601 文本；日期字段存 YYYY-MM-DD 文本。
- 金额/数量不使用 float 真值：M0 约定模型层用 TEXT 存 Decimal 序列化字符串
  （AnalysisResult 的 JSON 序列化中金额已是字符串，见 contracts.analysis 序列化约定）。
- CHECK 约束（数量>0、非负参数、日期先后、状态枚举）只在迁移内定义，
  本文件与 M0 一样只声明列/外键/唯一/索引（迁移才是 DDL 唯一权威）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utc_now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 文本（本地库时间统一为 UTC 文本）。"""
    return datetime.now(UTC).isoformat()


#: 本地库约束命名约定：约束显式命名，保证错误可定位、迁移可回滚
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: analysis_run.status 状态机取值（M0 冻结；新增状态须走“只加不改”迁移）
RUN_STATUS_CREATED = "CREATED"
RUN_STATUS_QUEUED = "QUEUED"
RUN_STATUS_RUNNING = "RUNNING"
RUN_STATUS_SUCCEEDED = "SUCCEEDED"
RUN_STATUS_FAILED = "FAILED"
RUN_STATUS_CANCELLED = "CANCELLED"

#: 全部合法状态（Repository 写入使用，UI 只读展示）
RUN_STATUSES: tuple[str, ...] = (
    RUN_STATUS_CREATED,
    RUN_STATUS_QUEUED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCEEDED,
    RUN_STATUS_FAILED,
    RUN_STATUS_CANCELLED,
)


class Base(DeclarativeBase):
    """本地库 declarative 基类（metadata 携带命名约定）。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class DecimalText(TypeDecorator[Decimal]):
    """TEXT 列存 Decimal 序列化字符串（M0 约定：金额/数量禁止 float 真值）。

    SQLite 的 NUMERIC 亲和会把小数文本转成二进制浮点存储，故沿用 M0 的
    TEXT 存储口径：写入 ``str(Decimal)``、读出 ``Decimal(text)``，全程无 float。
    """

    impl = Text
    cacheable = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return str(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return Decimal(value)


#: inventory_event.move_type 取值（回放语义表见 local_data.projection）：
#: 数量恒为正（迁移 CHECK），库存增减方向由 move_type 承载。
MOVE_INBOUND = "INBOUND"  # 入库：余额 +
MOVE_OUTBOUND = "OUTBOUND"  # 出库：余额 -
MOVE_RETURN = "RETURN"  # 退货入库：余额 +
MOVE_SCRAP = "SCRAP"  # 报废：余额 -
MOVE_ADJUSTMENT = "ADJUSTMENT"  # 盘点：quantity 记实盘数，按差额调整（M1 口径）
MOVE_TRANSFER_IN = "TRANSFER_IN"  # 调拨入仓：余额 +（M1 单仓口径同表简化）
MOVE_TRANSFER_OUT = "TRANSFER_OUT"  # 调拨出仓：余额 -
MOVE_REVERSAL = "REVERSAL"  # 冲销：反转 reversal_of 原事件方向

#: 全部合法 move_type（未知类型投影时记 Warning 跳过，不阻断）
MOVE_TYPES: tuple[str, ...] = (
    MOVE_INBOUND,
    MOVE_OUTBOUND,
    MOVE_RETURN,
    MOVE_SCRAP,
    MOVE_ADJUSTMENT,
    MOVE_TRANSFER_IN,
    MOVE_TRANSFER_OUT,
    MOVE_REVERSAL,
)

#: import_batch.status 枚举（迁移 CHECK 同步约束）
IMPORT_STATUS_RUNNING = "RUNNING"
IMPORT_STATUS_COMPLETED = "COMPLETED"
IMPORT_STATUS_FAILED = "FAILED"
IMPORT_STATUSES: tuple[str, ...] = (
    IMPORT_STATUS_RUNNING,
    IMPORT_STATUS_COMPLETED,
    IMPORT_STATUS_FAILED,
)

#: purchase_order.status 枚举（迁移 CHECK 同步约束；业务逻辑随 replenishment 落地）
PO_STATUS_DRAFT = "DRAFT"
PO_STATUS_ORDERED = "ORDERED"
PO_STATUS_RECEIVED = "RECEIVED"
PO_STATUS_CLOSED = "CLOSED"
PO_STATUS_CANCELLED = "CANCELLED"
PO_STATUSES: tuple[str, ...] = (
    PO_STATUS_DRAFT,
    PO_STATUS_ORDERED,
    PO_STATUS_RECEIVED,
    PO_STATUS_CLOSED,
    PO_STATUS_CANCELLED,
)


class AnalysisRun(Base):
    """分析运行（analysis_run）：run_id 唯一，记录引擎/公式版本与状态机。"""

    __tablename__ = "analysis_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    task_id: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[str | None] = mapped_column(Text)  # YYYY-MM-DD
    end_date: Mapped[str | None] = mapped_column(Text)  # YYYY-MM-DD
    scope_json: Mapped[str | None] = mapped_column(Text)  # 仓库范围等 scope 快照
    engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    formula_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str | None] = mapped_column(Text)  # UTC ISO 8601
    finished_at: Mapped[str | None] = mapped_column(Text)  # UTC ISO 8601
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso)

    # 双向关系：让单元工作流知道 run 先于 result 插入（FK 顺序），不改变表结构
    results: Mapped[list[AnalysisResultRow]] = relationship(back_populates="run")


class AnalysisResultRow(Base):
    """分析结果行（analysis_result）：(run_id, result_type, sku_id) 复合索引。

    M0 约定：result_type="full_result" 单行存 AnalysisResult 完整 JSON；
    metric_json 中金额/数量为字符串文本（Decimal 序列化，禁止 float 真值）。
    """

    __tablename__ = "analysis_result"
    __table_args__ = (
        Index("ix_analysis_result_run_id_result_type_sku_id", "run_id", "result_type", "sku_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(Text, ForeignKey("analysis_run.run_id"), nullable=False)
    result_type: Mapped[str] = mapped_column(Text, nullable=False)
    sku_id: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    metric_json: Mapped[str] = mapped_column(Text, nullable=False)
    warning_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)

    run: Mapped[AnalysisRun] = relationship(back_populates="results")


class MetaInfo(Base):
    """本地元信息（local_meta）：db_schema_version、install_instance_id、单主工作台标识。"""

    __tablename__ = "local_meta"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------------------
# 主数据组（0003_master_data，§35.4 主数据组 7 表）
# ---------------------------------------------------------------------------


class SkuRow(Base):
    """SKU（sku）：sku_id 唯一；category / is_active 索引（ABC 与过滤查询）。"""

    __tablename__ = "sku"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sku_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text, index=True)  # ix_sku_category
    sub_category: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)  # 计量单位名（如 瓶/箱）
    unit_scale: Mapped[Decimal | None] = mapped_column(DecimalText)  # 换算为基础单位的倍率
    unit_cost: Mapped[Decimal | None] = mapped_column(DecimalText)
    industry: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


class BarcodeRow(Base):
    """条码（barcode）：barcode 唯一，一条条码至多映射一个有效 SKU。"""

    __tablename__ = "barcode"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    barcode: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sku_id: Mapped[str] = mapped_column(Text, ForeignKey("sku.sku_id"), nullable=False)
    package_unit: Mapped[str | None] = mapped_column(Text)
    conversion_factor: Mapped[Decimal | None] = mapped_column(DecimalText)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


class WarehouseRow(Base):
    """仓库（warehouse）：warehouse_id 唯一。"""

    __tablename__ = "warehouse"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    warehouse_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


class LocationRow(Base):
    """库位（location）：(warehouse_id, location_id) 唯一。"""

    __tablename__ = "location"
    __table_args__ = (
        UniqueConstraint(
            "warehouse_id", "location_id", name="uq_location_warehouse_id_location_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    warehouse_id: Mapped[str] = mapped_column(
        Text, ForeignKey("warehouse.warehouse_id"), nullable=False
    )
    location_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


class SupplierRow(Base):
    """供应商（supplier）：supplier_id 唯一。"""

    __tablename__ = "supplier"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    supplier_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    contact: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


class SupplierSkuRow(Base):
    """供应商-SKU 补货参数（supplier_sku）：(supplier_id, sku_id) 组合主键。

    lead_time_days / moq / pack_size / order_cost / holding_cost 非负
    （迁移 CHECK）；业务语义随 replenishment 落地，M1 只建结构。
    """

    __tablename__ = "supplier_sku"

    supplier_id: Mapped[str] = mapped_column(
        Text, ForeignKey("supplier.supplier_id"), primary_key=True
    )
    sku_id: Mapped[str] = mapped_column(Text, ForeignKey("sku.sku_id"), primary_key=True)
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    moq: Mapped[Decimal | None] = mapped_column(DecimalText)
    pack_size: Mapped[Decimal | None] = mapped_column(DecimalText)
    order_cost: Mapped[Decimal | None] = mapped_column(DecimalText)
    holding_cost: Mapped[Decimal | None] = mapped_column(DecimalText)
    is_preferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


class LotRow(Base):
    """批次（lot）：(sku_id, lot_id) 唯一；有效期不得早于生产日期（迁移 CHECK）。

    production_date / expiry_date 存 YYYY-MM-DD 文本（ISO 日期字典序即时间序），
    received_at 存 UTC ISO 8601 文本。
    """

    __tablename__ = "lot"
    __table_args__ = (
        UniqueConstraint("sku_id", "lot_id", name="uq_lot_sku_id_lot_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    lot_id: Mapped[str] = mapped_column(Text, nullable=False)
    sku_id: Mapped[str] = mapped_column(Text, ForeignKey("sku.sku_id"), nullable=False)
    production_date: Mapped[str | None] = mapped_column(Text)  # YYYY-MM-DD
    expiry_date: Mapped[str | None] = mapped_column(Text)  # YYYY-MM-DD
    received_at: Mapped[str | None] = mapped_column(Text)  # UTC ISO 8601
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


# ---------------------------------------------------------------------------
# 库存事实组（0004_inventory_events，§35.4 业务事件组 6 表）
# ---------------------------------------------------------------------------


class InventoryEventRow(Base):
    """库存事件（inventory_event）：唯一库存事实来源，event_id 唯一（幂等导入）。

    - 数量必须大于 0（迁移 CHECK），方向由 move_type 承载（语义表见 projection）；
    - (sku_id, warehouse_id, occurred_at) 索引支撑期间明细查询；
    - location/lot 为可空维度：location_id 单列不唯一，故与 warehouse_id 组成
      复合外键引用 location(warehouse_id, location_id)；lot 同理引用 lot(sku_id, lot_id)。
    """

    __tablename__ = "inventory_event"
    __table_args__ = (
        Index(
            "ix_inventory_event_sku_id_warehouse_id_occurred_at",
            "sku_id",
            "warehouse_id",
            "occurred_at",
        ),
        ForeignKeyConstraint(
            ["warehouse_id", "location_id"],
            ["location.warehouse_id", "location.location_id"],
            name="fk_inventory_event_warehouse_id_location",
        ),
        ForeignKeyConstraint(
            ["sku_id", "lot_id"],
            ["lot.sku_id", "lot.lot_id"],
            name="fk_inventory_event_sku_id_lot",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sku_id: Mapped[str] = mapped_column(Text, ForeignKey("sku.sku_id"), nullable=False)
    warehouse_id: Mapped[str] = mapped_column(
        Text, ForeignKey("warehouse.warehouse_id"), nullable=False
    )
    location_id: Mapped[str | None] = mapped_column(Text)
    lot_id: Mapped[str | None] = mapped_column(Text)
    move_type: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(DecimalText)
    occurred_at: Mapped[str] = mapped_column(Text, nullable=False)  # UTC ISO 8601
    source: Mapped[str] = mapped_column(Text, nullable=False)  # 如 IMPORT_CSV / UI
    source_ref: Mapped[str | None] = mapped_column(Text)  # 源单据号等回溯信息
    reversal_of: Mapped[str | None] = mapped_column(
        Text, ForeignKey("inventory_event.event_id")
    )
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


class InventoryEventLineRow(Base):
    """库存事件行（inventory_event_line）：(event_id, line_no) 唯一，多 SKU 单据可扩展。"""

    __tablename__ = "inventory_event_line"
    __table_args__ = (
        UniqueConstraint("event_id", "line_no", name="uq_inventory_event_line_event_id_line_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(
        Text, ForeignKey("inventory_event.event_id"), nullable=False
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    sku_id: Mapped[str] = mapped_column(Text, ForeignKey("sku.sku_id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(DecimalText)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


class StockSnapshotRow(Base):
    """库存快照（stock_snapshot）：五维 (日期, sku, 仓, 库位, 批次) 唯一。"""

    __tablename__ = "stock_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "sku_id",
            "warehouse_id",
            "location_id",
            "lot_id",
            name="uq_stock_snapshot_date_sku_wh_loc_lot",
        ),
        ForeignKeyConstraint(
            ["warehouse_id", "location_id"],
            ["location.warehouse_id", "location.location_id"],
            name="fk_stock_snapshot_warehouse_id_location",
        ),
        ForeignKeyConstraint(
            ["sku_id", "lot_id"],
            ["lot.sku_id", "lot.lot_id"],
            name="fk_stock_snapshot_sku_id_lot",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    snapshot_date: Mapped[str] = mapped_column(Text, nullable=False)  # YYYY-MM-DD
    sku_id: Mapped[str] = mapped_column(Text, ForeignKey("sku.sku_id"), nullable=False)
    warehouse_id: Mapped[str] = mapped_column(
        Text, ForeignKey("warehouse.warehouse_id"), nullable=False
    )
    location_id: Mapped[str | None] = mapped_column(Text)
    lot_id: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    inventory_value: Mapped[Decimal | None] = mapped_column(DecimalText)
    source: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


class PurchaseOrderRow(Base):
    """采购订单（purchase_order）：po_id 唯一，状态枚举（迁移 CHECK）。

    M1 只建结构（表结构先建，业务逻辑随 replenishment 落地）。
    """

    __tablename__ = "purchase_order"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    po_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    supplier_id: Mapped[str] = mapped_column(
        Text, ForeignKey("supplier.supplier_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    ordered_at: Mapped[str | None] = mapped_column(Text)  # UTC ISO 8601
    expected_at: Mapped[str | None] = mapped_column(Text)  # UTC ISO 8601
    closed_at: Mapped[str | None] = mapped_column(Text)  # UTC ISO 8601
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


class PurchaseOrderLineRow(Base):
    """采购订单行（purchase_order_line）：(po_id, line_no) 唯一；数量非负（迁移 CHECK）。

    超收容差（received_qty 与 ordered_qty 的关系）属 replenishment 业务口径，
    M1 仅做非负下界约束。
    """

    __tablename__ = "purchase_order_line"
    __table_args__ = (
        UniqueConstraint("po_id", "line_no", name="uq_purchase_order_line_po_id_line_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    po_id: Mapped[str] = mapped_column(
        Text, ForeignKey("purchase_order.po_id"), nullable=False
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    sku_id: Mapped[str] = mapped_column(Text, ForeignKey("sku.sku_id"), nullable=False)
    ordered_qty: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    received_qty: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(DecimalText)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


class InventoryBalanceRow(Base):
    """库存余额（inventory_balance）：可整表重建的查询投影，非唯一事实来源。

    口径：身份维度 (sku_id, warehouse_id, location_id, lot_id)，不设业务 UNIQUE
    （SQLite 组合唯一对 NULL 维度不生效）；重建 = 清空后按事件全量回放写入，
    一行一个维度组合。as_of_event_id 为该维度最后回放到的事件。
    """

    __tablename__ = "inventory_balance"
    __table_args__ = (
        ForeignKeyConstraint(
            ["warehouse_id", "location_id"],
            ["location.warehouse_id", "location.location_id"],
            name="fk_inventory_balance_warehouse_id_location",
        ),
        ForeignKeyConstraint(
            ["sku_id", "lot_id"],
            ["lot.sku_id", "lot.lot_id"],
            name="fk_inventory_balance_sku_id_lot",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sku_id: Mapped[str] = mapped_column(Text, ForeignKey("sku.sku_id"), nullable=False)
    warehouse_id: Mapped[str] = mapped_column(
        Text, ForeignKey("warehouse.warehouse_id"), nullable=False
    )
    location_id: Mapped[str | None] = mapped_column(Text)
    lot_id: Mapped[str | None] = mapped_column(Text)
    on_hand_qty: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    available_qty: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    reserved_qty: Mapped[Decimal] = mapped_column(
        DecimalText, nullable=False, default=Decimal(0)
    )
    as_of_event_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("inventory_event.event_id")
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


# ---------------------------------------------------------------------------
# 导入治理组（0005_import，§35.4 分析和治理组 import_batch / import_error）
# ---------------------------------------------------------------------------


class ImportBatchRow(Base):
    """导入批次（import_batch）：状态机 RUNNING→COMPLETED/FAILED（迁移 CHECK 枚举）。

    file_hash 建索引：重复文件导入提示据此检出（不静默重复入库）。
    """

    __tablename__ = "import_batch"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_hash: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)  # 如 CSV
    started_at: Mapped[str] = mapped_column(Text, nullable=False)  # UTC ISO 8601
    completed_at: Mapped[str | None] = mapped_column(Text)  # UTC ISO 8601
    status: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


class ImportErrorRow(Base):
    """导入错误行（import_error）：按 (batch_id, row_no) 定位，含错误码与修复建议。

    raw_value 只落业务库用于错误修复闭环；禁止将敏感值写入技术日志（§35.4）。
    """

    __tablename__ = "import_error"
    __table_args__ = (
        Index("ix_import_error_batch_id_row_no", "batch_id", "row_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(
        Text, ForeignKey("import_batch.batch_id"), nullable=False
    )
    row_no: Mapped[int] = mapped_column(Integer, nullable=False)
    field_name: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str] = mapped_column(Text, nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[str | None] = mapped_column(Text)  # UTC ISO 8601
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


# ---------------------------------------------------------------------------
# 报告与备份组（0006_report_backup，§35.4 分析和治理组 report_artifact / backup_record）
# ---------------------------------------------------------------------------

#: report_artifact.format 取值（导出器支持的报告格式）
REPORT_FORMAT_HTML = "HTML"
REPORT_FORMAT_CSV = "CSV"
REPORT_FORMATS: tuple[str, ...] = (
    REPORT_FORMAT_HTML,
    REPORT_FORMAT_CSV,
)

#: backup_record.backup_type 枚举（迁移 CHECK 同步约束）
BACKUP_TYPE_MANUAL = "MANUAL"
BACKUP_TYPE_AUTO = "AUTO"
BACKUP_TYPES: tuple[str, ...] = (
    BACKUP_TYPE_MANUAL,
    BACKUP_TYPE_AUTO,
)

#: backup_record.status 枚举（迁移 CHECK 同步约束）
BACKUP_STATUS_CREATED = "CREATED"
BACKUP_STATUS_VERIFIED = "VERIFIED"
BACKUP_STATUS_FAILED = "FAILED"
BACKUP_STATUSES: tuple[str, ...] = (
    BACKUP_STATUS_CREATED,
    BACKUP_STATUS_VERIFIED,
    BACKUP_STATUS_FAILED,
)


class ReportArtifactRow(Base):
    """报告产物（report_artifact）：(run_id, format) 唯一，报告与 run 一一对应可追溯。

    重复导出语义：导出幂等重生成相同内容，重复导出 = 覆盖文件 + 更新记录
    （UNIQUE 冲突走更新路径，见 ReportArtifactRepository.upsert）。
    """

    __tablename__ = "report_artifact"
    __table_args__ = (
        UniqueConstraint("run_id", "format", name="uq_report_artifact_run_id_format"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("analysis_run.run_id"), nullable=False
    )
    format: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


class BackupRecordRow(Base):
    """备份记录（backup_record）：全量备份的登记与恢复校验依据。

    - db_schema_version 从备份时 local_meta/alembic_version 读取（恢复校验比对）；
    - status 状态机：CREATED（文件已生成）→ VERIFIED（读回复验通过）/
      FAILED（生成或复验失败）；VERIFIED 必须有 verified_at（§35.4）。
    """

    __tablename__ = "backup_record"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    backup_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    backup_type: Mapped[str] = mapped_column(Text, nullable=False)
    db_schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    verified_at: Mapped[str | None] = mapped_column(Text)  # UTC ISO 8601
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


# ---------------------------------------------------------------------------
# 小程序事件同步组（0007_sync_config，M3 Task 4）
# ---------------------------------------------------------------------------

#: sync_outbox.status 枚举（迁移 CHECK 同步约束）：
#: PENDING（落库成功、云端 ACK 未确认）→ ACKED（云端已确认）。
SYNC_ACK_STATUS_PENDING = "PENDING"
SYNC_ACK_STATUS_ACKED = "ACKED"
SYNC_ACK_STATUSES: tuple[str, ...] = (
    SYNC_ACK_STATUS_PENDING,
    SYNC_ACK_STATUS_ACKED,
)

#: sync_inbox.error_code 取值（解密/校验/落库失败分类）
SYNC_ERROR_DECRYPT_FAILED = "DECRYPT_FAILED"
SYNC_ERROR_APPLY_FAILED = "APPLY_FAILED"


class SyncInboxRow(Base):
    """同步收件箱（sync_inbox）：云端下拉信封的落地记录，event_id 主键幂等。

    - envelope_ciphertext 原样保留密文：解密失败（DECRYPT_FAILED）不丢数据，
      换钥或修复后可重放；apply_error / error_code 记录失败原因；
    - applied_at 为空即未成功落库（applied）。
    """

    __tablename__ = "sync_inbox"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    envelope_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    applied_at: Mapped[str | None] = mapped_column(Text)  # UTC ISO 8601
    apply_error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)


class SyncOutboxRow(Base):
    """同步发件箱（sync_outbox）：云端 ACK 待确认队列，ack_id（= 云端 envelope_id）主键。

    落库成功先记 PENDING，云端 ACK 成功后置 ACKED；ACK 失败不回滚落库，
    下轮循环重发 PENDING 的 ACK（云端 ACK 幂等）。
    """

    __tablename__ = "sync_outbox"
    __table_args__ = (
        Index("ix_sync_outbox_status", "status"),
    )

    ack_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[str] = mapped_column(
        Text, ForeignKey("sync_inbox.event_id"), nullable=False
    )
    acked_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    status: Mapped[str] = mapped_column(Text, nullable=False)
