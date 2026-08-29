# ER 图：本地业务库与云端控制库（M0 基线）

**归档目的**：关闭《项目开发文档评审报告.md》§8 修订清单第 1 项（P0-1）——"将主基线中的本地/云端 ER 关系归档为仓库文件"。
**对齐依据**：字段与约束 100% 取自实际迁移与 ORM 源码（非主基线示意稿）：

| 库 | 迁移文件 | ORM / 模型 |
|---|---|---|
| 本地业务库（SQLite） | `local-data/alembic/versions/0001_meta.py`、`local-data/alembic/versions/0002_analysis_m0.py` | `local-data/src/local_data/models.py` |
| 本地业务库（SQLite，M1 扩展） | `local-data/alembic/versions/0003_master_data.py`（revision `0003_master_data`）、`0004_inventory_events.py`（`0004_inventory_events`）、`0005_import.py`（`0005_import`）、`0006_report_backup.py`（`0006_report_backup`）——revision ID 一律下划线命名（禁止 `-`），链式 down_revision 顺延 | `local-data/src/local_data/models.py`（同 M0）；余额投影口径见 `local-data/src/local_data/projection.py` |
| 云端控制库（PostgreSQL） | `services/control-plane/alembic/versions/0001_control_meta.py`（revision `control_0001`） | M0 迁移手写、无 ORM；引入模型后挂载 Alembic 元数据 |

**M0 范围说明**：主基线 §35.2 的完整表组（本地 `sku`、`inventory_event`、`inventory_balance`、`sync_outbox` 等；云端商户/设备/许可/任务等业务表）尚未建表。本图只收录已落地迁移实际创建的表；后续迁移遵循"只加不改"，新增表/字段时须同步更新本图。

**M1 范围说明（追加）**：M1 迁移 0003-0006 已落地本地业务库 17 张表（主数据 7 + 事件 6 + 导入 2 + 报告备份 2），本图 §1 已扩展收录（M0 实体与关系线原样保留，M1 实体/关系为追加）；云端控制库 M1 无新增迁移（仍为 `control_0001`，无商户业务明细表）。本地 `sync_outbox` 等同步表组仍未建（随 M3 同步落地，届时继续只加不改扩展本图）。

---

## 1. 本地业务库（SQLite）

- **数据库文件**：`<数据目录>/warehouse.db`；数据目录解析优先级 = 显式参数 > `WORKBENCH_DATA_DIR` > `%LOCALAPPDATA%\WarehouseWorkbench\data`
- **连接**：`sqlite+pysqlite:///`；每个连接执行 `PRAGMA foreign_keys=ON`、`PRAGMA journal_mode=WAL`、`PRAGMA busy_timeout=5000`（见 `local_data.connection`）
- **存储约定**：时间字段为 UTC ISO 8601 文本；日期字段为 `YYYY-MM-DD` 文本；金额/数量以 TEXT 存 Decimal 序列化字符串（禁止 float 真值）
- **ORM**：`local_data.models`（SQLAlchemy 2.0 declarative，约束显式命名，保证错误可定位、迁移可回滚）

```mermaid
erDiagram
    local_meta["local_meta（迁移 0001_meta）"] {
        TEXT key PK "约束名 pk_local_meta"
        TEXT value "NOT NULL"
    }
    analysis_run["analysis_run（迁移 0002_analysis_m0）"] {
        INTEGER id PK "自增主键 pk_analysis_run"
        TEXT run_id UK "UNIQUE 约束 uq_analysis_run_run_id，NOT NULL"
        TEXT task_id "云端任务标识，M0 可空"
        TEXT start_date "分析期间起始日 YYYY-MM-DD"
        TEXT end_date "分析期间结束日 YYYY-MM-DD"
        TEXT scope_json "仓库范围等 scope 快照（JSON 文本）"
        TEXT engine_version "引擎版本，NOT NULL"
        TEXT formula_version "公式版本，NOT NULL"
        TEXT status "状态机取值，NOT NULL"
        TEXT started_at "开始时间 UTC ISO 8601"
        TEXT finished_at "结束时间 UTC ISO 8601"
        TEXT error_code "失败错误码，取 contracts.ErrorCode 值"
        TEXT created_at "创建时间 UTC ISO 8601，NOT NULL"
        TEXT updated_at "更新时间 UTC ISO 8601，NOT NULL"
    }
    analysis_result["analysis_result（迁移 0002_analysis_m0）"] {
        INTEGER id PK "自增主键 pk_analysis_result"
        TEXT run_id FK "约束名 fk_analysis_result_run_id_analysis_run，指向 analysis_run.run_id，NOT NULL"
        TEXT result_type "M0 固定 full_result，NOT NULL"
        TEXT sku_id "SKU 标识，full_result 单行存整体结果时为空"
        TEXT category "分类"
        TEXT metric_json "指标 JSON 文本，NOT NULL（金额/数量为 Decimal 字符串）"
        TEXT warning_json "警告 JSON 文本，NOT NULL（code/severity/message/fields/blocking 五要素）"
        TEXT created_at "创建时间 UTC ISO 8601，NOT NULL"
    }
    alembic_version["alembic_version（Alembic 自动维护）"] {
        VARCHAR version_num PK "upgrade head 后值为 0002_analysis_m0（M0 时点；M1 后 head 推进至 0006_report_backup，见 §1.3 图注）"
    }

    sku["sku（迁移 0003_master_data）"] {
        INTEGER id PK "自增主键 pk_sku"
        TEXT sku_id UK "UNIQUE 约束 uq_sku_sku_id，NOT NULL"
        TEXT name "名称，NOT NULL"
        TEXT category "品类，可空，索引 ix_sku_category"
        TEXT sub_category "子品类，可空"
        TEXT unit "计量单位名，可空"
        TEXT unit_scale "换算倍率，DecimalText，可空"
        TEXT unit_cost "单位成本，DecimalText，可空"
        TEXT industry "行业，可空"
        BOOLEAN is_active "有效标志 0/1，NOT NULL，索引 ix_sku_is_active"
        TEXT created_at "创建时间 UTC ISO 8601，NOT NULL"
        TEXT updated_at "更新时间 UTC ISO 8601，NOT NULL"
    }
    barcode["barcode（迁移 0003_master_data）"] {
        INTEGER id PK "自增主键 pk_barcode"
        TEXT barcode UK "UNIQUE 约束 uq_barcode_barcode，NOT NULL"
        TEXT sku_id FK "约束名 fk_barcode_sku_id_sku → sku.sku_id，NOT NULL"
        TEXT package_unit "包装单位，可空"
        TEXT conversion_factor "换算系数，DecimalText，可空"
        BOOLEAN is_active "有效标志 0/1，NOT NULL"
        TEXT created_at "创建时间，NOT NULL"
        TEXT updated_at "更新时间，NOT NULL"
    }
    warehouse["warehouse（迁移 0003_master_data）"] {
        INTEGER id PK "自增主键 pk_warehouse"
        TEXT warehouse_id UK "UNIQUE 约束 uq_warehouse_warehouse_id，NOT NULL"
        TEXT name "仓库名，NOT NULL"
        TEXT address "地址，可空"
        BOOLEAN is_active "有效标志 0/1，NOT NULL"
        TEXT created_at "创建时间，NOT NULL"
        TEXT updated_at "更新时间，NOT NULL"
    }
    location["location（迁移 0003_master_data）"] {
        INTEGER id PK "自增主键 pk_location"
        TEXT warehouse_id FK "约束名 fk_location_warehouse_id_warehouse → warehouse.warehouse_id，NOT NULL"
        TEXT location_id "库位标识，NOT NULL；与 warehouse_id 组成 UNIQUE uq_location_warehouse_id_location_id"
        TEXT name "库位名，可空"
        BOOLEAN is_active "有效标志 0/1，NOT NULL"
        TEXT created_at "创建时间，NOT NULL"
        TEXT updated_at "更新时间，NOT NULL"
    }
    supplier["supplier（迁移 0003_master_data）"] {
        INTEGER id PK "自增主键 pk_supplier"
        TEXT supplier_id UK "UNIQUE 约束 uq_supplier_supplier_id，NOT NULL"
        TEXT name "供应商名，NOT NULL"
        TEXT contact "联系方式，可空"
        BOOLEAN is_active "有效标志 0/1，NOT NULL"
        TEXT created_at "创建时间，NOT NULL"
        TEXT updated_at "更新时间，NOT NULL"
    }
    supplier_sku["supplier_sku（迁移 0003_master_data）"] {
        TEXT supplier_id PK "组合主键 pk_supplier_sku 列一，FK fk_supplier_sku_supplier_id_supplier → supplier.supplier_id"
        TEXT sku_id PK "组合主键 pk_supplier_sku 列二，FK fk_supplier_sku_sku_id_sku → sku.sku_id"
        INTEGER lead_time_days "供货周期天数，可空，CHECK 非负"
        TEXT moq "最小起订量，DecimalText，可空，CHECK 非负"
        TEXT pack_size "包装规格，DecimalText，可空，CHECK 非负"
        TEXT order_cost "订货成本，DecimalText，可空，CHECK 非负"
        TEXT holding_cost "持有成本，DecimalText，可空，CHECK 非负"
        BOOLEAN is_preferred "首选供应商标志，NOT NULL"
        TEXT created_at "创建时间，NOT NULL"
        TEXT updated_at "更新时间，NOT NULL"
    }
    lot["lot（迁移 0003_master_data）"] {
        INTEGER id PK "自增主键 pk_lot"
        TEXT lot_id "批次标识，NOT NULL；与 sku_id 组成 UNIQUE uq_lot_sku_id_lot_id"
        TEXT sku_id FK "约束名 fk_lot_sku_id_sku → sku.sku_id，NOT NULL"
        TEXT production_date "生产日期 YYYY-MM-DD，可空"
        TEXT expiry_date "到期日期 YYYY-MM-DD，可空，CHECK 不得早于生产日期"
        TEXT received_at "收货时间 UTC ISO 8601，可空"
        TEXT created_at "创建时间，NOT NULL"
        TEXT updated_at "更新时间，NOT NULL"
    }
    inventory_event["inventory_event（迁移 0004_inventory_events）"] {
        INTEGER id PK "自增主键 pk_inventory_event"
        TEXT event_id UK "UNIQUE 约束 uq_inventory_event_event_id，NOT NULL（幂等导入）"
        TEXT sku_id FK "约束名 fk_inventory_event_sku_id_sku → sku.sku_id，NOT NULL"
        TEXT warehouse_id FK "约束名 fk_inventory_event_warehouse_id_warehouse → warehouse.warehouse_id，NOT NULL；并参与复合 FK → location"
        TEXT location_id "库位维度，可空；复合 FK fk_inventory_event_warehouse_id_location：(warehouse_id, location_id) → location"
        TEXT lot_id "批次维度，可空；复合 FK fk_inventory_event_sku_id_lot：(sku_id, lot_id) → lot"
        TEXT move_type "事件类型，NOT NULL，取值见 data-dictionary.md §1.21"
        TEXT quantity "数量，DecimalText，NOT NULL，CHECK 大于 0"
        TEXT unit_cost "单位成本，DecimalText，可空"
        TEXT occurred_at "发生时间 UTC ISO 8601，NOT NULL；索引 ix_inventory_event_sku_id_warehouse_id_occurred_at"
        TEXT source "来源标识，NOT NULL（如 IMPORT_CSV）"
        TEXT source_ref "源单据号，可空"
        TEXT reversal_of "冲销指向，可空，自引用 FK fk_inventory_event_reversal_of_inventory_event → event_id"
        TEXT created_by "创建人，可空"
        TEXT created_at "创建时间，NOT NULL"
        TEXT updated_at "更新时间，NOT NULL"
    }
    inventory_event_line["inventory_event_line（迁移 0004_inventory_events）"] {
        INTEGER id PK "自增主键 pk_inventory_event_line"
        TEXT event_id FK "约束名 fk_inventory_event_line_event_id_inventory_event，NOT NULL；与 line_no 组成 UNIQUE uq_inventory_event_line_event_id_line_no"
        INTEGER line_no "行号，NOT NULL"
        TEXT sku_id FK "约束名 fk_inventory_event_line_sku_id_sku → sku.sku_id，NOT NULL"
        TEXT quantity "数量，DecimalText，NOT NULL"
        TEXT unit_cost "单位成本，DecimalText，可空"
        TEXT created_at "创建时间，NOT NULL"
        TEXT updated_at "更新时间，NOT NULL"
    }
    stock_snapshot["stock_snapshot（迁移 0004_inventory_events）"] {
        INTEGER id PK "自增主键 pk_stock_snapshot"
        TEXT snapshot_id UK "UNIQUE 约束 uq_stock_snapshot_snapshot_id，NOT NULL"
        TEXT snapshot_date "快照日 YYYY-MM-DD，NOT NULL；五元组 UNIQUE 之一"
        TEXT sku_id FK "约束名 fk_stock_snapshot_sku_id_sku → sku.sku_id，NOT NULL；五元组 UNIQUE 之一"
        TEXT warehouse_id FK "约束名 fk_stock_snapshot_warehouse_id_warehouse → warehouse.warehouse_id，NOT NULL；五元组 UNIQUE 之一"
        TEXT location_id "库位维度，可空，五元组 UNIQUE 之一；复合 FK fk_stock_snapshot_warehouse_id_location"
        TEXT lot_id "批次维度，可空，五元组 UNIQUE 之一；复合 FK fk_stock_snapshot_sku_id_lot"
        TEXT quantity "时点数量，DecimalText，NOT NULL"
        TEXT inventory_value "时点库存价值，DecimalText，可空"
        TEXT source "来源标识，可空"
        TEXT created_at "创建时间，NOT NULL"
        TEXT updated_at "更新时间，NOT NULL"
    }
    purchase_order["purchase_order（迁移 0004_inventory_events）"] {
        INTEGER id PK "自增主键 pk_purchase_order"
        TEXT po_id UK "UNIQUE 约束 uq_purchase_order_po_id，NOT NULL"
        TEXT supplier_id FK "约束名 fk_purchase_order_supplier_id_supplier → supplier.supplier_id，NOT NULL"
        TEXT status "状态枚举，NOT NULL，CHECK ck_purchase_order_status（DRAFT/ORDERED/RECEIVED/CLOSED/CANCELLED）"
        TEXT ordered_at "下单时间，可空"
        TEXT expected_at "预计到货时间，可空"
        TEXT closed_at "关闭时间，可空"
        TEXT created_at "创建时间，NOT NULL"
        TEXT updated_at "更新时间，NOT NULL"
    }
    purchase_order_line["purchase_order_line（迁移 0004_inventory_events）"] {
        INTEGER id PK "自增主键 pk_purchase_order_line"
        TEXT po_id FK "约束名 fk_purchase_order_line_po_id_purchase_order，NOT NULL；与 line_no 组成 UNIQUE uq_purchase_order_line_po_id_line_no"
        INTEGER line_no "行号，NOT NULL"
        TEXT sku_id FK "约束名 fk_purchase_order_line_sku_id_sku → sku.sku_id，NOT NULL"
        TEXT ordered_qty "订购数，DecimalText，NOT NULL，CHECK 非负"
        TEXT received_qty "已收数，DecimalText，NOT NULL，CHECK 非负"
        TEXT unit_cost "单位成本，DecimalText，可空"
        TEXT created_at "创建时间，NOT NULL"
        TEXT updated_at "更新时间，NOT NULL"
    }
    inventory_balance["inventory_balance（迁移 0004_inventory_events，查询投影）"] {
        INTEGER id PK "自增主键 pk_inventory_balance"
        TEXT sku_id FK "约束名 fk_inventory_balance_sku_id_sku → sku.sku_id，NOT NULL；身份维度之一"
        TEXT warehouse_id FK "约束名 fk_inventory_balance_warehouse_id_warehouse → warehouse.warehouse_id，NOT NULL；身份维度之二"
        TEXT location_id "库位维度，可空，身份维度之三；复合 FK fk_inventory_balance_warehouse_id_location"
        TEXT lot_id "批次维度，可空，身份维度之四；复合 FK fk_inventory_balance_sku_id_lot"
        TEXT on_hand_qty "账面在手量，DecimalText，NOT NULL（按事件回放结果）"
        TEXT available_qty "可用量，DecimalText，NOT NULL（M1 = on_hand_qty）"
        TEXT reserved_qty "预留量，DecimalText，NOT NULL（M1 恒 0）"
        TEXT as_of_event_id "该维度最后回放事件，可空，FK fk_inventory_balance_as_of_event_id_inventory_event"
        TEXT created_at "创建时间，NOT NULL"
        TEXT updated_at "更新时间，NOT NULL"
    }
    import_batch["import_batch（迁移 0005_import）"] {
        INTEGER id PK "自增主键 pk_import_batch"
        TEXT batch_id UK "UNIQUE 约束 uq_import_batch_batch_id，NOT NULL"
        TEXT file_name "文件名，NOT NULL"
        TEXT file_hash "文件 SHA-256，NOT NULL，索引 ix_import_batch_file_hash"
        TEXT source_type "来源类型，NOT NULL（如 CSV）"
        TEXT started_at "开始时间，NOT NULL"
        TEXT completed_at "完成时间，可空"
        TEXT status "状态枚举，NOT NULL，CHECK ck_import_batch_status（RUNNING/COMPLETED/FAILED）"
        INTEGER row_count "提交行数，NOT NULL"
        INTEGER error_count "错误行数，NOT NULL"
        TEXT created_at "创建时间，NOT NULL"
        TEXT updated_at "更新时间，NOT NULL"
    }
    import_error["import_error（迁移 0005_import，错误隔离）"] {
        INTEGER id PK "自增主键 pk_import_error"
        TEXT batch_id FK "约束名 fk_import_error_batch_id_import_batch → import_batch.batch_id，NOT NULL"
        INTEGER row_no "源文件行号，NOT NULL；索引 ix_import_error_batch_id_row_no"
        TEXT field_name "出错字段，可空"
        TEXT error_code "错误码，NOT NULL"
        TEXT raw_value "原始值，可空（只落业务库，禁入技术日志）"
        TEXT suggestion "修复建议，可空"
        TEXT resolved_at "修复闭环时间，可空"
        TEXT created_at "创建时间，NOT NULL"
        TEXT updated_at "更新时间，NOT NULL"
    }
    report_artifact["report_artifact（迁移 0006_report_backup）"] {
        INTEGER id PK "自增主键 pk_report_artifact"
        TEXT report_id UK "UNIQUE 约束 uq_report_artifact_report_id，NOT NULL"
        TEXT run_id FK "约束名 fk_report_artifact_run_id_analysis_run → analysis_run.run_id，NOT NULL；与 format 组成 UNIQUE uq_report_artifact_run_id_format"
        TEXT format "报告格式，NOT NULL（HTML/CSV）"
        TEXT file_path "产物路径，NOT NULL"
        TEXT sha256 "产物 SHA-256，NOT NULL"
        TEXT created_at "创建时间，NOT NULL"
        TEXT updated_at "更新时间，NOT NULL（重复导出更新）"
    }
    backup_record["backup_record（迁移 0006_report_backup）"] {
        INTEGER id PK "自增主键 pk_backup_record"
        TEXT backup_id UK "UNIQUE 约束 uq_backup_record_backup_id，NOT NULL"
        TEXT file_path "备份文件路径，NOT NULL"
        TEXT backup_type "类型枚举，NOT NULL，CHECK ck_backup_record_backup_type（MANUAL/AUTO）"
        TEXT db_schema_version "备份时 schema 版本，NOT NULL（恢复校验依据）"
        TEXT sha256 "备份文件 SHA-256，NOT NULL"
        INTEGER size_bytes "备份文件字节数，NOT NULL"
        TEXT status "状态枚举，NOT NULL，CHECK ck_backup_record_status（CREATED/VERIFIED/FAILED）"
        TEXT verified_at "复验时间，可空（VERIFIED 时必填）"
        TEXT created_at "创建时间，NOT NULL"
        TEXT updated_at "更新时间，NOT NULL"
    }

    analysis_run ||--o{ analysis_result : "run_id 1:N，复合索引 ix_analysis_result_run_id_result_type_sku_id"

    sku ||--o{ barcode : "sku_id 1:N"
    warehouse ||--o{ location : "warehouse_id 1:N（location_id 仓内唯一）"
    supplier ||--o{ supplier_sku : "supplier_id 1:N（组合主键列一）"
    sku ||--o{ supplier_sku : "sku_id 1:N（组合主键列二）"
    sku ||--o{ lot : "sku_id 1:N（lot_id SKU 内唯一）"
    sku ||--o{ inventory_event : "sku_id 1:N"
    warehouse ||--o{ inventory_event : "warehouse_id 1:N"
    location ||--o{ inventory_event : "复合外键 (warehouse_id, location_id) 1:N，location_id 可空"
    lot ||--o{ inventory_event : "复合外键 (sku_id, lot_id) 1:N，lot_id 可空"
    inventory_event ||--o{ inventory_event : "reversal_of 自引用 0..N（冲销指向原事件）"
    inventory_event ||--o{ inventory_event_line : "event_id 1:N（line_no 事件内唯一）"
    sku ||--o{ inventory_event_line : "sku_id 1:N"
    sku ||--o{ stock_snapshot : "sku_id 1:N"
    warehouse ||--o{ stock_snapshot : "warehouse_id 1:N"
    location ||--o{ stock_snapshot : "复合外键 (warehouse_id, location_id) 1:N，可空"
    lot ||--o{ stock_snapshot : "复合外键 (sku_id, lot_id) 1:N，可空"
    supplier ||--o{ purchase_order : "supplier_id 1:N"
    purchase_order ||--o{ purchase_order_line : "po_id 1:N（line_no 订单内唯一）"
    sku ||--o{ purchase_order_line : "sku_id 1:N"
    sku ||--o{ inventory_balance : "sku_id 1:N（投影，见 §1.3 图注）"
    warehouse ||--o{ inventory_balance : "warehouse_id 1:N（投影）"
    location ||--o{ inventory_balance : "复合外键 (warehouse_id, location_id) 1:N，可空（投影）"
    lot ||--o{ inventory_balance : "复合外键 (sku_id, lot_id) 1:N，可空（投影）"
    inventory_event ||--o{ inventory_balance : "as_of_event_id 0..N（该维度最后回放事件，投影）"
    analysis_run ||--o{ report_artifact : "run_id 1:N（(run_id, format) 唯一：每格式一条）"
    import_batch ||--o{ import_error : "batch_id 1:N（错误隔离，见 §1.3 图注）"
```

### 1.1 analysis_run.status 状态机（M0 冻结）

`CREATED / QUEUED / RUNNING / SUCCEEDED / FAILED / CANCELLED`（Repository 写入，UI 只读展示；新增状态须走"只加不改"迁移）。

### 1.2 种子数据（随迁移写入 local_meta）

| key | value | 写入迁移 |
|---|---|---|
| `db_schema_version` | `local-0002`（0002 升级后；0002 降级回 `local-0001`） | 0001_meta 写入，0002_analysis_m0 更新 |
| `install_instance_id` | UUID4（首次建库生成一次；downgrade base 后重新 upgrade 会生成新值） | 0001_meta |
| `single_primary_workbench` | `1`（单主工作台标识，§35.3 单写入进程原则） | 0001_meta |

### 1.3 M1 扩展图注（0003-0006）

- **alembic head 推进**：M1 迁移链 `0002_analysis_m0 → 0003_master_data → 0004_inventory_events → 0005_import → 0006_report_backup`，`upgrade head` 后 `alembic_version` 当前值为 **`0006_report_backup`**（图中 M0 实体注释 `0002_analysis_m0` 为 M0 时点值）；`local_meta.db_schema_version` 随每支迁移同步推进为 **`local-0006`**，`backup_record.db_schema_version` 记录备份时该值（恢复校验比对依据）。
- **inventory_balance 是投影，不是事实来源**：`inventory_event` 是唯一库存事实来源（事件先落库为真）；`inventory_balance` 为可整表重建的查询投影——重建 = 同一事务内清空余额表后按 `(occurred_at, event_id)` 稳定排序回放事件全量重写（任意次重建结果一致，不保存不可再生状态）。身份维度 `(sku_id, warehouse_id, location_id, lot_id)` **不设业务 UNIQUE**：SQLite 组合唯一对 NULL 维度不生效，且维度含可空列，唯一性无意义；NULL 维度（无库位/批次的事件）在投影中单独成组。图中的 `as_of_event_id` 关系线表示"该维度组合最后回放到的事件"（0..1，非引用完整性之外的语义）。M1 口径：`available_qty = on_hand_qty`、`reserved_qty` 恒 0；出库导致负库存不阻断回放，最终为负的维度组合记入投影告警返回。
- **import_error 是错误隔离表**：导入批次中校验失败的行按 `(batch_id, row_no)` 定位写入本表（含 `field_name` / `error_code` / `raw_value` / `suggestion`），**合法行正常入库、错误行不阻塞批次**（错误隔离原则）；`raw_value` 等敏感值只落本地业务库，禁止写入技术日志。`import_batch.row_count / error_count` 与本表行数对账，`file_hash` 索引用于重复文件导入提示（不静默重复入库）。
- **复合外键画法说明**：`location_id` / `lot_id` 在事件、快照、余额三表中为可空维度，Mermaid 关系线按复合外键整体绘制（`(warehouse_id, location_id) → location`、`(sku_id, lot_id) → lot`）；SQLite 对含 NULL 的复合外键不强制（MATCH SIMPLE 行为），NULL 维度即"未指定库位/批次"。
- **backup_record 与其余表无外键**：备份登记行独立于业务表（备份文件是整库快照，`db_schema_version` + SHA-256 + 关键表行数构成恢复校验依据），删除业务数据不影响备份记录的解释。

---

## 2. 云端控制库（PostgreSQL）

- **连接**：`DATABASE_URL`（默认 `postgresql+psycopg://postgres:postgres@localhost:5432/warehouse_control`）；本地开发用 `docker compose -f docker-compose.dev.yml up -d postgres`
- **迁移 revision**：`control_0001`（文件 `0001_control_meta.py`）；依据主基线 §35.7，M0 只建元数据与枚举字典，不建任何商户业务明细表
- **枚举设计**：`code` 采用 `<kind>:<value>` 形式保证全局唯一（不同 kind 可能重名，如 task_status 与 sync_status 都有 CREATED）

```mermaid
erDiagram
    control_meta["control_meta（迁移 0001_control_meta）"] {
        TEXT key PK "主键（约束未显式命名）"
        TEXT value "NOT NULL"
        TIMESTAMPTZ created_at "NOT NULL，server_default now()"
        TIMESTAMPTZ updated_at "NOT NULL，server_default now()"
    }
    control_enum["control_enum（迁移 0001_control_meta）"] {
        INTEGER id PK "自增主键"
        TEXT code UK "UNIQUE，形式 kind:value"
        TEXT kind "枚举类别，NOT NULL"
    }
    alembic_version["alembic_version（Alembic 自动维护）"] {
        VARCHAR version_num PK "upgrade head 后值为 control_0001（revision ID 不允许含 '-'）"
    }
```

`control_meta` 与 `control_enum` 之间无外键（两表职责独立：键值元信息 / 枚举字典，先删子表后删父表的顺序无依赖）。

### 2.1 种子数据

`control_meta`（2 行，随迁移 bulk_insert）：

| key | value |
|---|---|
| `db_schema_version` | `control-0001` |
| `control_plane_version` | `0.1.0` |

`control_enum`（36 行，`code = <kind>:<value>`）：

| kind | 取值 |
|---|---|
| `task_status` | CREATED、QUEUED、RUNNING、SUCCEEDED、FAILED、CANCELLED、MISSED、RETRYING |
| `run_status` | 同 task_status（与任务状态机同构） |
| `device_status` | REGISTERED、ONLINE、DEGRADED、OFFLINE、REVOKED |
| `sync_status` | CREATED、ENQUEUED、DELIVERED、APPLIED、ACKED、EXPIRED、REJECTED、RETRYING |
| `move_type` | INBOUND、OUTBOUND、RETURN、SCRAP、TRANSFER、STOCKTAKE、REVERSAL（取自 `contracts.MoveType`） |

---

## 3. 结构校验入口

`uv run python scripts/verify_schema.py`（仓库根执行）对本图两库的迁移结果做机器校验：

- 表存在性（本地 3 表 / 云端 2 表 + 各自 `alembic_version`）；
- 关键约束（`analysis_run.run_id` UNIQUE、`analysis_result` FK 与复合索引、`control_enum.code` UNIQUE、两库主键）；
- `alembic_version` 值（本地 `0002_analysis_m0` / 云端 `control_0001`）与种子数据（`db_schema_version` 等）；
- **M1 扩展（追加）**：本地 17 张新表存在性（0003 主数据 7 表、0004 事件 6 表、0005 导入 2 表、0006 报告备份 2 表）、关键约束（`sku.sku_id` UNIQUE、`inventory_event.event_id` UNIQUE 与 `quantity > 0` CHECK、快照五元组 UNIQUE、`report_artifact (run_id, format)` UNIQUE、`backup_record` 枚举 CHECK）及约束实际生效（重复/非法插入被拒）；本地 `alembic_version = 0006_report_backup`、`db_schema_version = local-0006`（M0 时点值随迁移链推进，云端部分不变）。

云端无本地 PostgreSQL 时自动打印 skip（连接探测失败不视为失败）；测试侧同口径验证见 `local-data/tests/test_migrations.py` 与 `services/control-plane/tests/test_migrations.py`。
