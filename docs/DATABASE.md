# 仓库品类分析决策工具 —— 数据库设计文档

| 项 | 值 |
|---|---|
| 文档版本 | **1.0** |
| 更新时间 | **2026-08-31** |
| 对应代码基线 | `master` @ `5f66454`（PR #12 合并后） |
| 文档性质 | 基于代码现状（Alembic 迁移 + ORM 模型 + 真实运行库）逆向梳理 |

## 版本基线

| 数据库 | 位置 | Schema 版本 | 迁移 HEAD | 引擎 |
|---|---|---|---|---|
| **本地业务库** | 商户本机 `%LOCALAPPDATA%\WarehouseWorkbench\data\warehouse.db` | **local-0006** | `0006_report_backup` | SQLite（WAL） |
| **云端控制库** | 开发者侧 PostgreSQL | **control-0004** | `control_0004` | PostgreSQL 14+ |

> `docs/compatibility-matrix.md` 中「Local DB schema = 3」是按主基线 §35.7 的**表组计数**口径登记，与本文的 **Alembic revision 链**口径（`local-0006`）并行存在，二者不冲突。

---

## 〇、两套库的分工（数据边界）

| 维度 | 本地业务库（SQLite） | 云端控制库（PostgreSQL） |
|---|---|---|
| 存放内容 | SKU、库存事件、快照、采购、分析运行/结果、导入批次与错误、报告产物、备份记录 | 商户、账号、会话、设备、行业类型、许可证、功能授权、审计日志、元信息与枚举字典 |
| 是否含业务明细 | ✅ 是（唯一落点） | ❌ **否**（DECISIONS.md D-011 隐私红线：云端不建 `sku`/`movement`/`unit_cost` 表） |
| 写入方 | 桌面工作台（单进程串行写入） | 控制平面 FastAPI |
| 迁移方式 | Alembic（启动时自动 `upgrade head`） | Alembic（手工 / 部署流程执行） |
| 备份 | 全量 `VACUUM INTO` + SHA-256 | 由 PostgreSQL 侧运维负责 |

---

# 第一部分：本地业务库（SQLite）

## 1.1 连接与存储约定

| 项 | 值 |
|---|---|
| 默认路径 | `%LOCALAPPDATA%\WarehouseWorkbench\data\warehouse.db`（DECISIONS.md D-002） |
| 覆盖变量 | `WORKBENCH_DATA_DIR` |
| 连接串 | `sqlite+pysqlite:///{data_dir}/warehouse.db` |
| 会话参数 | `sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)` |
| PRAGMA（每次连接执行） | `foreign_keys=ON`、`journal_mode=WAL`、`busy_timeout=5000` |
| 同目录附带 | `warehouse.db-wal`、`warehouse.db-shm`、`auth.json`（令牌）、`.workbench.lock`（单实例锁） |
| 兄弟目录 | `reports\`（报告产物）、`backups\`（备份文件） |
| 加密 | **第一版不启用 SQLCipher**（DECISIONS.md D-003），依赖 Windows 用户 ACL |

### 全局类型约定（对全部表生效）

1. **金额/数量列一律 `TEXT` 存 `Decimal` 序列化字符串**，禁止 float 真值。
   由 `local_data/models.py::DecimalText`（`TypeDecorator`）实现：
   ```python
   def process_bind_param(self, value, dialect): return None if value is None else str(value)
   def process_result_value(self, value, dialect): return None if value is None else Decimal(value)
   ```
   因此数值型 CHECK 一律写成 `CAST(col AS REAL) >= 0`。
   与契约层一致：JSON 传输亦为 Decimal 字符串（如 `"123.45"`）。
2. **时间列**存 UTC ISO 8601 文本（如 `2026-06-01T00:00:00+00:00`）；
   **日期列**存 `YYYY-MM-DD` 文本（字典序即时间序）。
3. 每表带 `created_at` / `updated_at`（`default=utc_now_iso`，`updated_at` 另带 `onupdate=utc_now_iso`）。
4. 除 `supplier_sku`（复合主键）外，每张业务表都有自增代理主键 `id INTEGER PRIMARY KEY`。
5. 约束命名约定 `NAMING_CONVENTION`：
   ```python
   {"ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s"}
   ```
   > ⚠️ CHECK 落库名会出现**双前缀**：迁移里声明 `ck_inventory_event_quantity_positive`，实际落库为 `ck_inventory_event_ck_inventory_event_quantity_positive`（`scripts/verify_schema.py` 已注明）。
6. **DDL 唯一权威是 Alembic 迁移**，不是 ORM 的 `create_all()`。ORM 模型不声明 `server_default`，默认值只在迁移中。

## 1.2 迁移链

```text
0001_meta ──► 0002_analysis_m0 ──► 0003_master_data ──► 0004_inventory_events ──► 0005_import ──► 0006_report_backup (HEAD)
```

`local_meta` 中记录：`db_schema_version = local-0006`、`install_instance_id = <UUID>`、`single_primary_workbench = 1`。

## 1.3 数据表清单（20 张业务表 + 1 张迁移表）

| # | 表名 | 分组 | 迁移 | 业务含义 | 事实/投影 |
|---|---|---|---|---|---|
| 1 | `local_meta` | 元信息 | 0001 | 本地库元信息 KV | — |
| 2 | `analysis_run` | 分析 | 0002 | 一次分析运行的元数据 | 事实 |
| 3 | `analysis_result` | 分析 | 0002 | 分析结果（整份 JSON） | 事实 |
| 4 | `sku` | 主数据 | 0003 | SKU 主数据 | 事实 |
| 5 | `barcode` | 主数据 | 0003 | 条码 | 事实 |
| 6 | `warehouse` | 主数据 | 0003 | 仓库 | 事实 |
| 7 | `location` | 主数据 | 0003 | 库位 | 事实 |
| 8 | `supplier` | 主数据 | 0003 | 供应商 | 事实 |
| 9 | `supplier_sku` | 主数据 | 0003 | 供应商-SKU 补货参数 | 事实 |
| 10 | `lot` | 主数据 | 0003 | 批次 | 事实 |
| 11 | `inventory_event` | 事件 | 0004 | **库存事件（唯一事实来源）** | 事实 |
| 12 | `inventory_event_line` | 事件 | 0004 | 事件明细行（多 SKU 单据扩展） | 事实 |
| 13 | `stock_snapshot` | 事件 | 0004 | 库存时点快照 | 事实 |
| 14 | `purchase_order` | 事件 | 0004 | 采购订单 | 事实 |
| 15 | `purchase_order_line` | 事件 | 0004 | 采购订单行 | 事实 |
| 16 | `inventory_balance` | 事件 | 0004 | 库存余额（**可重建投影**） | 投影 |
| 17 | `import_batch` | 导入 | 0005 | 导入批次 | 事实 |
| 18 | `import_error` | 导入 | 0005 | 导入错误行与修复建议 | 事实 |
| 19 | `report_artifact` | 报告/备份 | 0006 | 报告产物登记 | 事实 |
| 20 | `backup_record` | 报告/备份 | 0006 | 备份登记与校验状态 | 事实 |
| — | `alembic_version` | 基础设施 | — | 迁移版本 | — |

---

## 1.4 字段定义

> 图例：**PK** 主键 / **FK** 外键 / **UQ** 唯一 / **IX** 索引 / **NN** 非空。
> 「类型」列为 SQLite 落库类型；Decimal 列物理为 `TEXT`。

### 1.4.1 `local_meta` —— 本地库元信息

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `key` | TEXT | ❌ | — | **PK** | 元信息键：`db_schema_version` / `install_instance_id` / `single_primary_workbench` |
| `value` | TEXT | ❌ | — | — | 元信息值 |

### 1.4.2 `analysis_run` —— 分析运行

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | 代理主键 |
| `run_id` | TEXT | ❌ | — | **UQ** `uq_analysis_run_run_id` | 运行标识，格式 `run-{uuid4 前 12 位}` |
| `task_id` | TEXT | ✅ | NULL | — | 关联云端任务（M3 预留） |
| `start_date` | TEXT | ✅ | NULL | — | 期间起点 `YYYY-MM-DD` |
| `end_date` | TEXT | ✅ | NULL | — | 期间终点 `YYYY-MM-DD`（**左闭右开**） |
| `scope_json` | TEXT | ✅ | NULL | — | 仓库范围快照，M0/M1 恒为 NULL |
| `engine_version` | TEXT | ❌ | — | — | 引擎版本（如 `0.2.0`） |
| `formula_version` | TEXT | ❌ | — | — | 公式口径版本（如 `0.1.0`） |
| `status` | TEXT | ❌ | — | — | 见 §1.5.1 |
| `started_at` | TEXT | ✅ | NULL | — | UTC ISO 8601 |
| `finished_at` | TEXT | ✅ | NULL | — | UTC ISO 8601 |
| `error_code` | TEXT | ✅ | NULL | — | 失败时的错误码 |
| `created_at` | TEXT | ❌ | `utc_now_iso()` | — | UTC ISO 8601 |
| `updated_at` | TEXT | ❌ | `utc_now_iso()` | `onupdate` | UTC ISO 8601 |

### 1.4.3 `analysis_result` —— 分析结果

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | 代理主键 |
| `run_id` | TEXT | ❌ | — | **FK** → `analysis_run.run_id` | 所属运行 |
| `result_type` | TEXT | ❌ | — | **IX**（复合首位） | M1 固定 `full_result` |
| `sku_id` | TEXT | ✅ | NULL | **IX**（复合第 3 位） | 维度预留（`full_result` 行为空） |
| `category` | TEXT | ✅ | NULL | — | 维度预留 |
| `metric_json` | TEXT | ❌ | — | — | 整份 `AnalysisResult.model_dump_json()` |
| `warning_json` | TEXT | ❌ | — | — | warnings JSON 数组（冗余便于直查） |
| `created_at` | TEXT | ❌ | `utc_now_iso()` | — | UTC ISO 8601 |

索引：`ix_analysis_result_run_id_result_type_sku_id`（`run_id`, `result_type`, `sku_id`）

### 1.4.4 `sku` —— SKU 主数据

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | 代理主键 |
| `sku_id` | TEXT | ❌ | — | **UQ** `uq_sku_sku_id` | SKU 业务标识 |
| `name` | TEXT | ❌ | — | — | 名称 |
| `category` | TEXT | ✅ | NULL | **IX** `ix_sku_category` | 品类 |
| `sub_category` | TEXT | ✅ | NULL | — | 子品类 |
| `unit` | TEXT | ✅ | NULL | — | 计量单位名（如 瓶/箱） |
| `unit_scale` | TEXT(Decimal) | ✅ | NULL | — | 换算为基础单位的倍率 |
| `unit_cost` | TEXT(Decimal) | ✅ | NULL | — | 单位成本，非负 |
| `industry` | TEXT | ✅ | NULL | — | 所属行业 |
| `is_active` | BOOLEAN | ❌ | `1` | **IX** `ix_sku_is_active` | 是否启用 |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | 时间 audit |

### 1.4.5 `barcode` —— 条码

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | — |
| `barcode` | TEXT | ❌ | — | **UQ** `uq_barcode_barcode` | 条码 |
| `sku_id` | TEXT | ❌ | — | **FK** → `sku.sku_id` | 所属 SKU |
| `package_unit` | TEXT | ✅ | NULL | — | 包装单位 |
| `conversion_factor` | TEXT(Decimal) | ✅ | NULL | — | 换算系数 |
| `is_active` | BOOLEAN | ❌ | `1` | — | — |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | — |

### 1.4.6 `warehouse` —— 仓库

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | — |
| `warehouse_id` | TEXT | ❌ | — | **UQ** `uq_warehouse_warehouse_id` | 仓库业务标识（启动时自动种子 `WH-01`「主仓」） |
| `name` | TEXT | ❌ | — | — | 仓库名称 |
| `address` | TEXT | ✅ | NULL | — | 地址 |
| `is_active` | BOOLEAN | ❌ | `1` | — | — |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | — |

### 1.4.7 `location` —— 库位

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | — |
| `warehouse_id` | TEXT | ❌ | — | **UQ**（复合）**FK** → `warehouse.warehouse_id` | 所属仓库 |
| `location_id` | TEXT | ❌ | — | **UQ**（复合）`uq_location_warehouse_id_location_id` | 库位标识 |
| `name` | TEXT | ✅ | NULL | — | 库位名称 |
| `is_active` | BOOLEAN | ❌ | `1` | — | — |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | — |

### 1.4.8 `supplier` —— 供应商

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | — |
| `supplier_id` | TEXT | ❌ | — | **UQ** `uq_supplier_supplier_id` | 供应商标识 |
| `name` | TEXT | ❌ | — | — | 名称 |
| `contact` | TEXT | ✅ | NULL | — | 联系方式 |
| `is_active` | BOOLEAN | ❌ | `1` | — | — |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | — |

### 1.4.9 `supplier_sku` —— 供应商-SKU 补货参数（**复合主键，无代理 id**）

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `supplier_id` | TEXT | ❌ | — | **PK**（复合）**FK** → `supplier.supplier_id` | 供应商 |
| `sku_id` | TEXT | ❌ | — | **PK**（复合）**FK** → `sku.sku_id` | SKU |
| `lead_time_days` | INTEGER | ✅ | NULL | `ck_..._lead_time_nonneg`（`>= 0`） | 采购提前期（天） |
| `moq` | TEXT(Decimal) | ✅ | NULL | `cast(real) >= 0` | 最小起订量 |
| `pack_size` | TEXT(Decimal) | ✅ | NULL | `cast(real) >= 0` | 包装规格 |
| `order_cost` | TEXT(Decimal) | ✅ | NULL | `cast(real) >= 0` | 订购成本（EOQ 用） |
| `holding_cost` | TEXT(Decimal) | ✅ | NULL | `cast(real) >= 0` | 持有成本（EOQ 用） |
| `is_preferred` | BOOLEAN | ❌ | `0` | — | 是否首选供应商 |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | — |

### 1.4.10 `lot` —— 批次

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | — |
| `lot_id` | TEXT | ❌ | — | **UQ**（复合）`uq_lot_sku_id_lot_id` | 批次号 |
| `sku_id` | TEXT | ❌ | — | **UQ**（复合）**FK** → `sku.sku_id` | 所属 SKU |
| `production_date` | TEXT | ✅ | NULL | — | 生产日期 `YYYY-MM-DD` |
| `expiry_date` | TEXT | ✅ | NULL | `ck_..._expiry_not_before_production`（`expiry_date >= production_date`） | 到期日期 |
| `received_at` | TEXT | ✅ | NULL | — | 收货时间，UTC ISO 8601 |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | — |

### 1.4.11 `inventory_event` —— 库存事件（**唯一事实来源**）

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | — |
| `event_id` | TEXT | ❌ | — | **UQ** `uq_inventory_event_event_id` | 事件唯一标识（导入幂等键） |
| `sku_id` | TEXT | ❌ | — | **FK** → `sku.sku_id` | 所属 SKU |
| `warehouse_id` | TEXT | ❌ | — | **FK** → `warehouse.warehouse_id` | 仓库 |
| `location_id` | TEXT | ✅ | NULL | **复合 FK**（与 warehouse_id）→ `location` | 库位 |
| `lot_id` | TEXT | ✅ | NULL | **复合 FK**（与 sku_id）→ `lot` | 批次 |
| `move_type` | TEXT | ❌ | — | — | 见 §1.5.2（8 值） |
| `quantity` | TEXT(Decimal) | ❌ | — | `cast(real) > 0` （**恒正**） | 数量；方向由 `move_type` 表达 |
| `unit_cost` | TEXT(Decimal) | ✅ | NULL | — | 单位成本，非负 |
| `occurred_at` | TEXT | ❌ | — | **IX**（复合） | 发生时刻，UTC ISO 8601 |
| `source` | TEXT | ❌ | — | — | 来源：`IMPORT` / `IMPORT_CSV` / `UI` / `DESKTOP` / `MINI_PROGRAM` / `ADJUSTMENT` |
| `source_ref` | TEXT | ✅ | NULL | — | 源单据号（回溯用） |
| `reversal_of` | TEXT | ✅ | NULL | **自引用 FK** → `inventory_event.event_id` | 冲销指向的原事件 |
| `created_by` | TEXT | ✅ | NULL | — | 创建人 |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | — |

索引：`ix_inventory_event_sku_id_warehouse_id_occurred_at`（`sku_id`, `warehouse_id`, `occurred_at`）

### 1.4.12 `inventory_event_line` —— 事件明细行

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | — |
| `event_id` | TEXT | ❌ | — | **UQ**（复合）**FK** → `inventory_event.event_id` | 所属事件 |
| `line_no` | INTEGER | ❌ | — | **UQ**（复合）`uq_inventory_event_line_event_id_line_no` | 行号 |
| `sku_id` | TEXT | ❌ | — | **FK** → `sku.sku_id` | SKU |
| `quantity` | TEXT(Decimal) | ❌ | — | — | 数量 |
| `unit_cost` | TEXT(Decimal) | ✅ | NULL | — | 单位成本 |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | — |

### 1.4.13 `stock_snapshot` —— 库存快照

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | — |
| `snapshot_id` | TEXT | ❌ | — | **UQ** `uq_stock_snapshot_snapshot_id` | 快照标识 |
| `snapshot_date` | TEXT | ❌ | — | **UQ**（五元组，见下） | 快照日期 `YYYY-MM-DD` |
| `sku_id` | TEXT | ❌ | — | **UQ**（五元组）**FK** → `sku.sku_id` | SKU |
| `warehouse_id` | TEXT | ❌ | — | **UQ**（五元组）**FK** → `warehouse.warehouse_id` | 仓库 |
| `location_id` | TEXT | ✅ | NULL | **UQ**（五元组）**复合 FK** → `location` | 库位 |
| `lot_id` | TEXT | ✅ | NULL | **UQ**（五元组）**复合 FK** → `lot` | 批次 |
| `quantity` | TEXT(Decimal) | ❌ | — | — | 时点库存，**可为负**（负快照触发 `NEGATIVE_BALANCE` 警告） |
| `inventory_value` | TEXT(Decimal) | ✅ | NULL | — | 库存价值；单位成本缺失时作为**价值回退口径** |
| `source` | TEXT | ✅ | NULL | — | 来源 |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | — |

五元组唯一：`uq_stock_snapshot_date_sku_wh_loc_lot`（`snapshot_date`, `sku_id`, `warehouse_id`, `location_id`, `lot_id`）

### 1.4.14 `purchase_order` / `purchase_order_line`

**`purchase_order`**

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | — |
| `po_id` | TEXT | ❌ | — | **UQ** `uq_purchase_order_po_id` | 采购单号 |
| `supplier_id` | TEXT | ❌ | — | **FK** → `supplier.supplier_id` | 供应商 |
| `status` | TEXT | ❌ | — | `ck_..._status`（`DRAFT`/`ORDERED`/`RECEIVED`/`CLOSED`/`CANCELLED`） | 单据状态 |
| `ordered_at` | TEXT | ✅ | NULL | — | 下单时间，UTC ISO 8601 |
| `expected_at` | TEXT | ✅ | NULL | — | 预计到货，UTC ISO 8601 |
| `closed_at` | TEXT | ✅ | NULL | — | 关闭时间，UTC ISO 8601 |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | — |

**`purchase_order_line`**

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | — |
| `po_id` | TEXT | ❌ | — | **UQ**（复合）**FK** → `purchase_order.po_id` | 所属单据 |
| `line_no` | INTEGER | ❌ | — | **UQ**（复合）`uq_purchase_order_line_po_id_line_no` | 行号 |
| `sku_id` | TEXT | ❌ | — | **FK** → `sku.sku_id` | SKU |
| `ordered_qty` | TEXT(Decimal) | ❌ | — | `cast(real) >= 0` | 订购量 |
| `received_qty` | TEXT(Decimal) | ❌ | — | `cast(real) >= 0` | 已收量 |
| `unit_cost` | TEXT(Decimal) | ✅ | NULL | — | 单位成本 |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | — |

### 1.4.15 `inventory_balance` —— 库存余额（**可整表重建的投影**）

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | — |
| `sku_id` | TEXT | ❌ | — | **FK** → `sku.sku_id` | SKU |
| `warehouse_id` | TEXT | ❌ | — | **FK** → `warehouse.warehouse_id` | 仓库 |
| `location_id` | TEXT | ✅ | NULL | **复合 FK** → `location` | 库位 |
| `lot_id` | TEXT | ✅ | NULL | **复合 FK** → `lot` | 批次 |
| `on_hand_qty` | TEXT(Decimal) | ❌ | — | — | 账面在手量 |
| `available_qty` | TEXT(Decimal) | ❌ | — | — | 可用量 |
| `reserved_qty` | TEXT(Decimal) | ❌ | `0` | — | 预留量 |
| `as_of_event_id` | TEXT | ✅ | NULL | **FK** → `inventory_event.event_id` | 最后回放到的事件（增量续算水位） |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | — |

> **设计要点**：本表**不是事实来源**，可由 `inventory_event` 全量重放重建（`BalanceProjectionService.rebuild()`）。SQLite 组合唯一对含 NULL 的维度不生效，故本表**不加业务唯一约束**。M1 事件中若 `reversal_of` 为 NULL，重建时记 `REVERSAL_TARGET_MISSING` 告警并按 0 处理。

### 1.4.16 `import_batch` —— 导入批次

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | — |
| `batch_id` | TEXT | ❌ | — | **UQ** `uq_import_batch_batch_id` | 批次标识，格式 `IMP-{uuid4 前 12 位}` |
| `file_name` | TEXT | ❌ | — | — | 源文件名 |
| `file_hash` | TEXT | ❌ | — | **IX** `ix_import_batch_file_hash` | 文件 SHA-256（重复导入检出） |
| `source_type` | TEXT | ❌ | — | — | M1 固定 `CSV` |
| `started_at` | TEXT | ❌ | — | — | 开始时间，UTC ISO 8601 |
| `completed_at` | TEXT | ✅ | NULL | — | 完成时间，UTC ISO 8601 |
| `status` | TEXT | ❌ | — | `ck_..._status`（`RUNNING`/`COMPLETED`/`FAILED`） | 见 §1.5.3 |
| `row_count` | INTEGER | ❌ | `0` | — | 总行数 |
| `error_count` | INTEGER | ❌ | `0` | — | 错误行数 |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | — |

### 1.4.17 `import_error` —— 导入错误行

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | — |
| `batch_id` | TEXT | ❌ | — | **IX**（复合）**FK** → `import_batch.batch_id` | 所属批次 |
| `row_no` | INTEGER | ❌ | — | **IX**（复合）`ix_import_error_batch_id_row_no` | CSV 行号（**表头为第 1 行，数据首行 = 2**） |
| `field_name` | TEXT | ✅ | NULL | — | 出错字段 |
| `error_code` | TEXT | ❌ | — | — | 见 §1.5.4 |
| `raw_value` | TEXT | ✅ | NULL | — | 原始值（**仅落业务库，禁止写入技术日志**） |
| `suggestion` | TEXT | ✅ | NULL | — | 中文修复建议 |
| `resolved_at` | TEXT | ✅ | NULL | — | 修复时间，UTC ISO 8601 |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | — |

### 1.4.18 `report_artifact` —— 报告产物

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | — |
| `report_id` | TEXT | ❌ | — | **UQ** `uq_report_artifact_report_id` | 产物标识，格式 `report-{uuid4 前 12 位}` |
| `run_id` | TEXT | ❌ | — | **UQ**（复合）**FK** → `analysis_run.run_id` | 所属运行 |
| `format` | TEXT | ❌ | — | **UQ**（复合）`uq_report_artifact_run_id_format` | `HTML` / `CSV` |
| `file_path` | TEXT | ❌ | — | — | 产物绝对路径 |
| `sha256` | TEXT | ❌ | — | — | 产物 SHA-256（完整性校验） |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | — |

> 重复导出走 **upsert 更新**路径（更新 `report_id`/`file_path`/`sha256`/`updated_at`），不产生重复记录。

### 1.4.19 `backup_record` —— 备份记录

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | INTEGER | ❌ | 自增 | **PK** | — |
| `backup_id` | TEXT | ❌ | — | **UQ** `uq_backup_record_backup_id` | 备份标识，格式 `bak-{uuid4 前 12 位}` |
| `file_path` | TEXT | ❌ | — | — | 备份文件路径（`backups/backup-{时间戳}-{backup_id}.db`） |
| `backup_type` | TEXT | ❌ | — | `ck_..._backup_type`（`MANUAL`/`AUTO`） | 手动 / 恢复前自动安全备份 |
| `db_schema_version` | TEXT | ❌ | — | — | 备份时的 Schema 版本（恢复前比对） |
| `sha256` | TEXT | ❌ | — | — | 备份文件 SHA-256（恢复前比对） |
| `size_bytes` | INTEGER | ❌ | — | — | 文件大小 |
| `status` | TEXT | ❌ | — | `ck_..._status`（`CREATED`/`VERIFIED`/`FAILED`） | 见 §1.5.5 |
| `verified_at` | TEXT | ✅ | NULL | — | 复验通过时间；**`VERIFIED` 必须有值** |
| `created_at` / `updated_at` | TEXT | ❌ | `utc_now_iso()` | — | — |

---

## 1.5 枚举字典

### 1.5.1 `analysis_run.status`

| 值 | 含义 | 当前是否写入 |
|---|---|---|
| `CREATED` | 已创建 | ❌ 预留 |
| `QUEUED` | 排队中 | ❌ 预留 |
| `RUNNING` | 运行中 | ❌ 预留 |
| `SUCCEEDED` | 成功 | ✅ 唯一写入值 |
| `FAILED` | 失败 | ❌ 预留 |
| `CANCELLED` | 已取消 | ❌ 预留 |

### 1.5.2 `inventory_event.move_type`（本地库口径，8 值）

| 值 | 余额方向 | 语义 |
|---|---|---|
| `INBOUND` | +q | 入库 |
| `OUTBOUND` | −q | 出库 |
| `RETURN` | +q | 退货入库 |
| `SCRAP` | −q | 报废 |
| `ADJUSTMENT` | 差额 | 盘点：`quantity` 记实盘数，调整量 = 实盘数 − 当前账面 |
| `TRANSFER_IN` | +q | 调拨入仓 |
| `TRANSFER_OUT` | −q | 调拨出仓 |
| `REVERSAL` | 反转 | 冲销：按 `reversal_of` 原事件取反方向 |

> **与契约层的差异**：契约 `MoveType` 为 7 值（`INBOUND`/`OUTBOUND`/`RETURN`/`SCRAP`/`TRANSFER`/`STOCKTAKE`/`REVERSAL`）。二者由 `SqliteDatasetAdapter` 映射：
> `TRANSFER_IN`/`TRANSFER_OUT` → `TRANSFER`；`ADJUSTMENT` → `STOCKTAKE`；其余同名直映。

### 1.5.3 `import_batch.status`

`RUNNING` → `COMPLETED`；若 `row_count > 0 且 error_count == row_count` → `FAILED`。

### 1.5.4 `import_error.error_code`

| 错误码 | 中文标签 | 触发条件 |
|---|---|---|
| `QUANTITY_INVALID` | 数量非法 | 数量 ≤ 0 |
| `SKU_NOT_FOUND` | SKU 不存在 | 事件导入时 `sku_id` 不在 `sku` 表 |
| `WAREHOUSE_NOT_FOUND` | 仓库不存在 | 事件导入时 `warehouse_id` 不在 `warehouse` 表 |
| `DATE_INVALID` | 日期非法 | 非严格 `YYYY-MM-DD` |
| `MOVE_TYPE_INVALID` | 事件类型非法 | `move_type` 不在 8 值内 |
| `DECIMAL_INVALID` | 数值非法 | Decimal 解析失败或 NaN/Infinity |
| `UNIT_COST_INVALID` | 单位成本非法 | `unit_cost` < 0 |
| `REQUIRED_FIELD_MISSING` | 必填字段缺失 | 必填字段为空 |
| `SKU_DUPLICATE` | SKU 重复 | 主数据 create-only 撞 `uq_sku_sku_id` |

> 另有 3 个**阻断级**错误不落 `import_error`（不创建批次）：`IMPORT_ENCODING_FAILED`、`MISSING_REQUIRED_COLUMN`、`EMPTY_FILE`。

### 1.5.5 `backup_record.status`

`CREATED`（文件已生成）→ `VERIFIED`（读回复验通过，写 `verified_at`）/ `FAILED`（生成或复验失败）。

---

## 1.6 主键 / 外键 / 索引设计

### 1.6.1 主键策略

| 策略 | 表 | 理由 |
|---|---|---|
| **自增代理主键 `id` + 业务唯一键** | 18 张 | 外键引用稳定；业务键可变更 |
| **业务主键（无代理 id）** | `supplier_sku` | 纯关联表，无独立生命周期 |
| **自然主键** | `local_meta`（`key`）、`alembic_version`（`version_num`） | KV 表 |

### 1.6.2 外键与引用完整性

连接时执行 `PRAGMA foreign_keys=ON`，**外键真实生效**。全部外键：

| 子表 | 子表列 | 父表 | 父表列 | 说明 |
|---|---|---|---|---|
| `analysis_result` | `run_id` | `analysis_run` | `run_id` | 结果归属运行 |
| `barcode` | `sku_id` | `sku` | `sku_id` | |
| `location` | `warehouse_id` | `warehouse` | `warehouse_id` | |
| `supplier_sku` | `supplier_id` | `supplier` | `supplier_id` | |
| `supplier_sku` | `sku_id` | `sku` | `sku_id` | |
| `lot` | `sku_id` | `sku` | `sku_id` | |
| `inventory_event` | `sku_id` | `sku` | `sku_id` | |
| `inventory_event` | `warehouse_id` | `warehouse` | `warehouse_id` | |
| `inventory_event` | `(warehouse_id, location_id)` | `location` | `(warehouse_id, location_id)` | **复合外键** |
| `inventory_event` | `(sku_id, lot_id)` | `lot` | `(sku_id, lot_id)` | **复合外键** |
| `inventory_event` | `reversal_of` | `inventory_event` | `event_id` | **自引用** |
| `inventory_event_line` | `event_id` | `inventory_event` | `event_id` | |
| `inventory_event_line` | `sku_id` | `sku` | `sku_id` | |
| `stock_snapshot` | `sku_id` | `sku` | `sku_id` | |
| `stock_snapshot` | `warehouse_id` | `warehouse` | `warehouse_id` | |
| `stock_snapshot` | `(warehouse_id, location_id)` | `location` | `(warehouse_id, location_id)` | |
| `stock_snapshot` | `(sku_id, lot_id)` | `lot` | `(sku_id, lot_id)` | |
| `purchase_order` | `supplier_id` | `supplier` | `supplier_id` | |
| `purchase_order_line` | `po_id` | `purchase_order` | `po_id` | |
| `purchase_order_line` | `sku_id` | `sku` | `sku_id` | |
| `inventory_balance` | `sku_id` | `sku` | `sku_id` | |
| `inventory_balance` | `warehouse_id` | `warehouse` | `warehouse_id` | |
| `inventory_balance` | `(warehouse_id, location_id)` | `location` | `(warehouse_id, location_id)` | |
| `inventory_balance` | `(sku_id, lot_id)` | `lot` | `(sku_id, lot_id)` | |
| `inventory_balance` | `as_of_event_id` | `inventory_event` | `event_id` | 投影水位 |
| `import_error` | `batch_id` | `import_batch` | `batch_id` | |
| `report_artifact` | `run_id` | `analysis_run` | `run_id` | |

> **实践注意**：导入流程采用「前置依赖检测 + 逐行存在性校验」双保险，避免让插入撞外键；错误行进 `import_error`，合法行正常入库（错误隔离）。

### 1.6.3 索引清单

| 索引名 | 表 | 列 | 类型 | 用途 |
|---|---|---|---|---|
| `ix_analysis_result_run_id_result_type_sku_id` | `analysis_result` | `run_id`, `result_type`, `sku_id` | 非唯一 | 按运行取结果 |
| `ix_sku_category` | `sku` | `category` | 非唯一 | 品类维度分析 |
| `ix_sku_is_active` | `sku` | `is_active` | 非唯一 | 启用过滤 |
| `ix_inventory_event_sku_id_warehouse_id_occurred_at` | `inventory_event` | `sku_id`, `warehouse_id`, `occurred_at` | 非唯一 | **主查询路径**：按 SKU+仓+时间取事件 |
| `ix_import_batch_file_hash` | `import_batch` | `file_hash` | 非唯一 | 重复文件检出 |
| `ix_import_error_batch_id_row_no` | `import_error` | `batch_id`, `row_no` | 非唯一 | 错误行定位 |

其余唯一约束由 SQLite 自动创建 `sqlite_autoindex_*`：
`uq_analysis_run_run_id`、`uq_sku_sku_id`、`uq_barcode_barcode`、`uq_warehouse_warehouse_id`、`uq_location_warehouse_id_location_id`、`uq_supplier_supplier_id`、`uq_lot_sku_id_lot_id`、`uq_inventory_event_event_id`、`uq_inventory_event_line_event_id_line_no`、`uq_stock_snapshot_snapshot_id`、`uq_stock_snapshot_date_sku_wh_loc_lot`、`uq_purchase_order_po_id`、`uq_purchase_order_line_po_id_line_no`、`uq_import_batch_batch_id`、`uq_report_artifact_report_id`、`uq_report_artifact_run_id_format`、`uq_backup_record_backup_id`。

### 1.6.4 CHECK 约束清单

| 表 | 落库约束名 | 表达式 |
|---|---|---|
| `supplier_sku` | `ck_supplier_sku_ck_supplier_sku_lead_time_nonneg` | `lead_time_days >= 0` |
| `supplier_sku` | `ck_supplier_sku_ck_supplier_sku_moq_nonneg` | `CAST(moq AS REAL) >= 0` |
| `supplier_sku` | `ck_supplier_sku_ck_supplier_sku_pack_size_nonneg` | `CAST(pack_size AS REAL) >= 0` |
| `supplier_sku` | `ck_supplier_sku_ck_supplier_sku_order_cost_nonneg` | `CAST(order_cost AS REAL) >= 0` |
| `supplier_sku` | `ck_supplier_sku_ck_supplier_sku_holding_cost_nonneg` | `CAST(holding_cost AS REAL) >= 0` |
| `lot` | `ck_lot_ck_lot_expiry_not_before_production` | `expiry_date >= production_date` |
| `inventory_event` | `ck_inventory_event_ck_inventory_event_quantity_positive` | `CAST(quantity AS REAL) > 0` |
| `purchase_order` | `ck_purchase_order_ck_purchase_order_status` | `status IN ('DRAFT','ORDERED','RECEIVED','CLOSED','CANCELLED')` |
| `purchase_order_line` | `ck_purchase_order_line_..._ordered_qty_nonneg` | `CAST(ordered_qty AS REAL) >= 0` |
| `purchase_order_line` | `ck_purchase_order_line_..._received_qty_nonneg` | `CAST(received_qty AS REAL) >= 0` |
| `import_batch` | `ck_import_batch_ck_import_batch_status` | `status IN ('RUNNING','COMPLETED','FAILED')` |
| `backup_record` | `ck_backup_record_ck_backup_record_backup_type` | `backup_type IN ('MANUAL','AUTO')` |
| `backup_record` | `ck_backup_record_ck_backup_record_status` | `status IN ('CREATED','VERIFIED','FAILED')` |

---

# 第二部分：云端控制库（PostgreSQL）

## 2.1 连接与迁移约定

| 项 | 值 |
|---|---|
| 驱动 | `postgresql+psycopg`（**同步**驱动，SQLAlchemy 2.0） |
| 连接串 | `DATABASE_URL`，默认 `postgresql+psycopg://postgres:postgres@localhost:5432/warehouse_control` |
| 连接超时 | `connect_args={"connect_timeout": 3}`（避免 `/health` 被不可达地址拖住） |
| 迁移 | Alembic 手写（`alembic/env.py` 中 `target_metadata = None`，**禁用 autogenerate**） |
| 建表 | **仅由 Alembic 迁移负责**，代码中无 `Base.metadata.create_all()` |
| 仓储切换 | `CONTROL_PLANE_REPOSITORY=memory` 走内存实现（生产环境禁止），其余走 PostgreSQL |

> ⚠️ **ORM 与 DDL 权威分离**：`app/infrastructure/db/models.py` 未声明任何 `server_default`、CHECK、复合唯一、索引与外键 `ondelete`；这些**只在迁移中**。用 `create_all()` 建出的表会缺失上述全部约束。

## 2.2 迁移链

```text
control_0001 ──► control_0002 ──► control_0003 ──► control_0004 (HEAD)
   元信息+枚举     租户/账号/会话/设备    行业类型/许可证/功能授权    审计日志
```

`control_meta.db_schema_version` 随迁移推进：`control-0001` → `control-0003` → `control-0004`。
（注意：Alembic revision ID 不含 `-`，而 `control_meta` 的业务版本号含 `-`。）

## 2.3 数据表清单（10 张）

| # | 表名 | 迁移 | 业务含义 | 有 ORM 模型 |
|---|---|---|---|---|
| 1 | `control_meta` | 0001 | 控制库元信息 KV（Schema 版本、控制平面版本） | ❌ |
| 2 | `control_enum` | 0001 | 基础枚举字典（承接后续所有 seed） | ❌ |
| 3 | `tenant` | 0002 | 商户 | ✅ |
| 4 | `account` | 0002 | 账号 | ✅ |
| 5 | `session` | 0002 | 登录会话 | ✅ |
| 6 | `device` | 0002 | 设备 | ✅ |
| 7 | `product_profile` | 0003 | 行业类型 | ✅ |
| 8 | `license` | 0003 | 许可证 | ✅ |
| 9 | `feature_grant` | 0003 | 功能授权 | ✅ |
| 10 | `audit_log` | 0004 | 审计日志 | ✅ |

## 2.4 字段定义

> 图例同上。「默认值」列为迁移中的 `server_default`。

### 2.4.1 `control_meta`

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `key` | Text | ❌ | — | **PK** | `db_schema_version` / `control_plane_version` |
| `value` | Text | ❌ | — | — | 值 |
| `created_at` | TIMESTAMPTZ | ❌ | `now()` | — | — |
| `updated_at` | TIMESTAMPTZ | ❌ | `now()` | — | — |

种子：`{"db_schema_version": "control-0001", "control_plane_version": "0.1.0"}`

### 2.4.2 `control_enum`

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `id` | Integer（自增） | ❌ | — | **PK** | — |
| `code` | Text | ❌ | — | **UQ** | 格式 `<kind>:<value>`，保证全局唯一 |
| `kind` | Text | ❌ | — | — | 枚举类别 |

种子共 **59 行**：

| kind | 值 | 数量 | 来源 |
|---|---|---|---|
| `task_status` | `CREATED, QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED, MISSED, RETRYING` | 8 | 0001 |
| `run_status` | 同上 | 8 | 0001 |
| `device_status` | `REGISTERED, ONLINE, DEGRADED, OFFLINE, REVOKED` | 5 | 0001 |
| `sync_status` | `CREATED, ENQUEUED, DELIVERED, APPLIED, ACKED, EXPIRED, REJECTED, RETRYING` | 8 | 0001 |
| `move_type` | `INBOUND, OUTBOUND, RETURN, SCRAP, TRANSFER, STOCKTAKE, REVERSAL` | 7 | 0001 |
| `tenant_status` | `ACTIVE, SUSPENDED` | 2 | 0002 |
| `account_status` | `ACTIVE, LOCKED, DISABLED` | 3 | 0002 |
| `account_role` | `MERCHANT_OWNER, DEVELOPER` | 2 | 0002 |
| `client_type` | `DESKTOP, WEB, MINI_PROGRAM` | 3 | 0002 |
| `product_profile_status` | `ACTIVE, RETIRED` | 2 | 0003 |
| `license_status` | `ACTIVE, EXPIRED, REVOKED` | 3 | 0003 |
| `audit_action` | `AUTH_LOGIN, AUTH_REFRESH, AUTH_LOGOUT, DEVICE_REGISTER, LICENSE_STATUS_CHANGE` | 5 | 0004 |
| `audit_result` | `SUCCESS, DENIED, ERROR` | 3 | 0004 |

### 2.4.3 `tenant` —— 商户

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `tenant_id` | Text | ❌ | — | **PK** `pk_tenant` | 格式 `tnt_<uuid4hex>` |
| `name` | Text | ❌ | — | — | 商户名称 |
| `product_profile_id` | Text | ✅ | NULL | **FK** → `product_profile.product_profile_id`，`ON DELETE SET NULL`（0003 补建） | 行业类型 |
| `status` | Text | ❌ | `'ACTIVE'` | `ck_tenant_status`（`ACTIVE`/`SUSPENDED`） | 商户状态 |
| `created_at` / `updated_at` | TIMESTAMPTZ | ❌ | `now()` | — | — |

### 2.4.4 `account` —— 账号

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `account_id` | Text | ❌ | — | **PK** `pk_account` | 格式 `acc_<uuid4hex>` |
| `tenant_id` | Text | ✅ | NULL | **FK** → `tenant.tenant_id`，`ON DELETE CASCADE`；**IX** `ix_account_tenant_id` | 开发者账号为 NULL |
| `login_name` | Text | ❌ | — | **UQ** `uq_account_login_name` | 登录名 |
| `password_hash` | Text | ❌ | — | — | Argon2id 哈希 |
| `role` | Text | ❌ | — | `ck_account_role`（`MERCHANT_OWNER`/`DEVELOPER`） | 角色 |
| `status` | Text | ❌ | `'ACTIVE'` | `ck_account_status`（`ACTIVE`/`LOCKED`/`DISABLED`） | 账号状态 |
| `failed_attempts` | Integer | ❌ | `0` | `ck_account_failed_attempts_non_negative`（`>= 0`） | 连续失败次数（5 次锁定） |
| `locked_until` | TIMESTAMPTZ | ✅ | NULL | — | 锁定到期（15 分钟） |
| `last_login_at` | TIMESTAMPTZ | ✅ | NULL | — | 最近登录 |
| `created_at` / `updated_at` | TIMESTAMPTZ | ❌ | `now()` | — | — |

跨字段 CHECK：`ck_account_tenant_required_for_merchant` = `role = 'DEVELOPER' OR tenant_id IS NOT NULL`

### 2.4.5 `session` —— 登录会话（**一次登录一行**）

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `session_id` | Text | ❌ | — | **PK** `pk_session` | 格式 `ses_<uuid4hex>` |
| `account_id` | Text | ❌ | — | **FK** → `account.account_id`，`ON DELETE CASCADE`；**IX** `ix_session_account_id` | 所属账号 |
| `client_type` | Text | ❌ | — | `ck_session_client_type`（`DESKTOP`/`WEB`/`MINI_PROGRAM`） | 客户端类型 |
| `device_id` | Text | ✅ | NULL | **无外键**（可指向已删除设备或为空） | 设备 |
| `refresh_token_hash` | Text | ❌ | — | **UQ** `uq_session_refresh_token_hash` | 当前 Refresh Token 指纹 |
| `previous_refresh_token_hash` | Text | ✅ | NULL | — | 上一次指纹（**重放检测**） |
| `expires_at` | TIMESTAMPTZ | ❌ | — | **IX** `ix_session_expires_at` | 会话到期（首次登录 +30 天） |
| `rotated_at` | TIMESTAMPTZ | ✅ | NULL | — | 最近轮换时间 |
| `revoked_at` | TIMESTAMPTZ | ✅ | NULL | — | 撤销时间（**终态，无 un-revoke**） |
| `created_at` | TIMESTAMPTZ | ❌ | `now()` | — | — |

### 2.4.6 `device` —— 设备

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `device_id` | Text | ❌ | — | **PK** `pk_device` | 格式 `dev_<uuid4hex>` |
| `tenant_id` | Text | ❌ | — | **FK** → `tenant.tenant_id`，`ON DELETE CASCADE`；**IX**（复合） | 所属商户 |
| `device_type` | Text | ❌ | — | `ck_device_device_type`（`DESKTOP`/`WEB`/`MINI_PROGRAM`） | 类型 |
| `name` | Text | ❌ | — | — | 设备名称 |
| `fingerprint` | Text | ❌ | — | **UQ**（复合）`uq_device_tenant_id_fingerprint` | 设备指纹 |
| `status` | Text | ❌ | `'REGISTERED'` | `ck_device_status`（`REGISTERED`/`ONLINE`/`DEGRADED`/`OFFLINE`/`REVOKED`）；**IX**（复合） | 状态 |
| `app_version` | Text | ✅ | NULL | — | 客户端版本 |
| `last_seen_at` | TIMESTAMPTZ | ✅ | NULL | — | 最近心跳（**M2 心跳为 stub，永不更新**） |
| `registered_at` | TIMESTAMPTZ | ❌ | `now()` | — | 注册时间 |
| `updated_at` | TIMESTAMPTZ | ❌ | `now()` | — | — |

索引：`ix_device_tenant_id_status`（`tenant_id`, `status`）

### 2.4.7 `product_profile` —— 行业类型

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `product_profile_id` | Text | ❌ | — | **PK** `pk_product_profile` | 格式 `ppf_<uuid4hex>` |
| `code` | Text | ❌ | — | **UQ** `uq_product_profile_code` | 行业编码 |
| `name` | Text | ❌ | — | — | 行业名称 |
| `default_config_version` | Text | ✅ | NULL | — | 默认配置版本 |
| `status` | Text | ❌ | `'ACTIVE'` | `ck_product_profile_status`（`ACTIVE`/`RETIRED`） | 状态 |
| `created_at` | TIMESTAMPTZ | ❌ | `now()` | — | — |

### 2.4.8 `license` —— 许可证

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `license_id` | Text | ❌ | — | **PK** `pk_license` | 格式 `lic_<uuid4hex>` |
| `tenant_id` | Text | ❌ | — | **FK** → `tenant.tenant_id`，`ON DELETE CASCADE` | 所属商户 |
| `product_profile_id` | Text | ❌ | — | **FK** → `product_profile.product_profile_id`，`ON DELETE RESTRICT` | 行业类型 |
| `starts_at` | **Date** | ❌ | — | `ck_license_period_order`（`starts_at <= expires_at`） | 生效日 |
| `expires_at` | **Date** | ❌ | — | 同上 | 到期日 |
| `max_devices` | Integer | ❌ | `1` | `ck_license_max_devices_positive`（`> 0`） | 设备数上限 |
| `status` | Text | ❌ | `'ACTIVE'` | `ck_license_status`（`ACTIVE`/`EXPIRED`/`REVOKED`） | 状态 |
| `created_at` / `updated_at` | TIMESTAMPTZ | ❌ | `now()` | — | — |

**部分唯一索引**（PostgreSQL 特性）：
```sql
CREATE UNIQUE INDEX uq_license_tenant_active ON license (tenant_id) WHERE status = 'ACTIVE';
```
→ 同一商户只能有一个 ACTIVE 许可证，历史 `EXPIRED`/`REVOKED` 记录可并存。

> **设计说明**：离线宽限天数**不落表**（避免与运行配置双写），由运行配置 `LICENSE_OFFLINE_GRACE_DAYS`（默认 7）在 `domain/license.evaluate` 中按「到期日 + 宽限天数」推导。

### 2.4.9 `feature_grant` —— 功能授权

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `feature_grant_id` | Text | ❌ | — | **PK** `pk_feature_grant` | 格式 `fgr_<uuid4hex>` |
| `tenant_id` | Text | ❌ | — | **FK** → `tenant.tenant_id`，`ON DELETE CASCADE` | 所属商户 |
| `feature_code` | Text | ❌ | — | **UQ**（复合）`uq_feature_grant_tenant_id_feature_code` | 功能编码 |
| `enabled` | Boolean | ❌ | `true` | — | 是否启用 |
| `source` | Text | ❌ | `'LICENSE'` | — | 授权来源 |
| `expires_at` | TIMESTAMPTZ | ✅ | NULL | — | 授权到期 |
| `created_at` | TIMESTAMPTZ | ❌ | `now()` | — | — |

> 两种仓储实现的 `list_feature_grants` **均只返回 `enabled = true`** 的授权。

### 2.4.10 `audit_log` —— 审计日志（**只追加**）

| 字段 | 类型 | 可空 | 默认值 | 约束 | 注释 |
|---|---|---|---|---|---|
| `audit_id` | Text | ❌ | — | **PK** `pk_audit_log` | 格式 `aud_<uuid4hex>` |
| `actor_account_id` | Text | ✅ | NULL | **IX**（复合）；**无外键** | 操作者（登录失败等无账号场景可空） |
| `actor_role` | Text | ✅ | NULL | — | 操作者角色 |
| `tenant_id` | Text | ✅ | NULL | **IX**（复合）；**无外键** | 商户（开发者动作可空） |
| `action` | Text | ❌ | — | `ck_audit_log_action`（5 值，见 §2.5.1） | 动作 |
| `target_type` | Text | ✅ | NULL | — | 目标类型 |
| `target_id` | Text | ✅ | NULL | — | 目标标识 |
| `result` | Text | ❌ | — | `ck_audit_log_result`（`SUCCESS`/`DENIED`/`ERROR`） | 结果 |
| `request_id` | Text | ✅ | NULL | — | 关联 `X-Request-ID` |
| `detail_json` | JSON | ✅ | NULL | **白名单过滤** | 详情（见 §2.6） |
| `occurred_at` | TIMESTAMPTZ | ❌ | `now()` | — | 发生时间 |

索引：`ix_audit_log_tenant_occurred`（`tenant_id`, `occurred_at`）、`ix_audit_log_actor_occurred`（`actor_account_id`, `occurred_at`）

> 保留周期：迁移文档串声明 **180 天**，但**清理任务未实现**。

## 2.5 云端枚举

### 2.5.1 `audit_log.action`

| 值 | 含义 |
|---|---|
| `AUTH_LOGIN` | 登录 |
| `AUTH_REFRESH` | 刷新令牌 |
| `AUTH_LOGOUT` | 注销 |
| `DEVICE_REGISTER` | 设备注册 |
| `LICENSE_STATUS_CHANGE` | 许可证状态变更 |

### 2.5.2 领域枚举（不落 `control_enum` 的运行时派生值）

| 枚举 | 取值 |
|---|---|
| `EntitlementStatus`（许可证评估结论） | `ACTIVE` / `GRACE` / `EXPIRED` / `REVOKED` / `MISSING` |
| `AccountRole` | `MERCHANT_OWNER` / `DEVELOPER` |
| `ClientType` | `DESKTOP` / `WEB` / `MINI_PROGRAM` |
| `DeviceStatus` | `REGISTERED` / `ONLINE` / `DEGRADED` / `OFFLINE` / `REVOKED` |

> **放行策略**：`ACTIVE` / `GRACE` 放行；`EXPIRED` / `REVOKED` / `MISSING` 拒绝（403 `LICENSE_EXPIRED`）。

## 2.6 审计 `detail_json` 字段白名单（9 个键）

| 键 | 含义 |
|---|---|
| `reason` | 拒绝/失败原因码（如 `REFRESH_TOKEN_REUSED`、`DEVICE_LIMIT_EXCEEDED`、`TENANT_SUSPENDED`） |
| `client_type` | 客户端类型 |
| `device_type` | 设备类型 |
| `device_name` | 设备名称（用户自定义，非业务数据） |
| `license_id` | 许可证标识 |
| `from_status` | 状态迁移前 |
| `to_status` | 状态迁移后 |
| `account_status` | 账号状态（拒绝时说明原因，不含凭证） |
| `tenant_id` | 商户标识 |

**双重过滤**：① 键必须在白名单内，越界键静默丢弃（不抛错，避免审计拖垮业务主流程）；② 值必须是标量（`None`/`str`/`int`/`float`/`bool`），非标量丢弃（防嵌套业务数据）。

## 2.7 云端索引与约束汇总

| 对象名 | 类型 | 定义 |
|---|---|---|
| `pk_*`（10 张表） | PK | 各表主键 |
| `uq_account_login_name` | UNIQUE | `account(login_name)` |
| `uq_session_refresh_token_hash` | UNIQUE | `session(refresh_token_hash)` |
| `uq_device_tenant_id_fingerprint` | UNIQUE | `device(tenant_id, fingerprint)` |
| `uq_product_profile_code` | UNIQUE | `product_profile(code)` |
| `uq_feature_grant_tenant_id_feature_code` | UNIQUE | `feature_grant(tenant_id, feature_code)` |
| `uq_license_tenant_active` | UNIQUE（部分） | `license(tenant_id) WHERE status='ACTIVE'` |
| `ix_account_tenant_id` | INDEX | `account(tenant_id)` |
| `ix_session_account_id` / `ix_session_expires_at` | INDEX | `session` |
| `ix_device_tenant_id_status` | INDEX | `device(tenant_id, status)` |
| `ix_audit_log_tenant_occurred` / `ix_audit_log_actor_occurred` | INDEX | `audit_log` |
| `ck_*`（12 个） | CHECK | 见 §2.4 各表 |

## 2.8 标识符生成规则

统一 `<prefix>_<uuid4 hex>`（32 位无连字符）：

| 前缀 | 表.主键 |
|---|---|
| `tnt` | `tenant.tenant_id` |
| `acc` | `account.account_id` |
| `ses` | `session.session_id` |
| `dev` | `device.device_id` |
| `lic` | `license.license_id` |
| `ppf` | `product_profile.product_profile_id` |
| `fgr` | `feature_grant.feature_grant_id` |
| `aud` | `audit_log.audit_id` |

本地库侧同类规则：`run-{uuid4前12位}`、`IMP-{uuid4前12位}`、`bak-{uuid4前12位}`、`report-{uuid4前12位}`。

---

# 第三部分：表间关系（ER 说明）

## 3.1 本地业务库 ER

```text
                       ┌──────────┐
                       │ warehouse│──┐ 1:N
                       └────┬─────┘  │
                    1:N     │        ▼
              ┌─────────────▼──┐  ┌──────────┐
              │    location    │  │  sku     │◄──┐ 1:N（被引用最多的实体）
              │ (wh_id,loc_id) │  └────┬─────┘   │
              └────────────────┘       │         │
                                       │ 1:N     │
        ┌──────────────┬───────────────┼─────────┼──────────────┐
        ▼              ▼               ▼         ▼              ▼
  ┌───────────┐  ┌──────────┐  ┌──────────┐ ┌───────┐  ┌────────────┐
  │   lot     │  │ barcode  │  │supplier_ │ │ ...   │  │stock_      │
  │(sku,lot)  │  │          │  │  sku     │ │       │  │snapshot    │
  └─────┬─────┘  └──────────┘  └────▲─────┘ └───────┘  └────────────┘
        │                           │ N:1
        │ 复合FK                    │
        │      ┌────────────────────┴──┐
        └─────►│    inventory_event    │◄──┐ 自引用（reversal_of）
               │ ★ 唯一库存事实来源     │───┘
               └───────┬───────────────┘
                       │ 1:N                    ┌──────────┐
                       ▼                        │ supplier │
              ┌──────────────────┐              └────▲─────┘
              │inventory_event_  │                   │ 1:N
              │     line         │              ┌────┴──────────┐
              └──────────────────┘              │purchase_order │
                                                └────┬──────────┘
               ┌───────────────────┐                 │ 1:N
               │ inventory_balance │                 ▼
               │ ★ 可重建投影      │          ┌──────────────────┐
               └───────────────────┘          │purchase_order_line│
                       ▲                      └──────────────────┘
                       │ 由事件重放生成
                       └────────（非外键依赖，可清空重建）

  ┌──────────────┐        ┌───────────────┐       ┌─────────────────┐
  │ import_batch │──1:N──►│ import_error  │       │  analysis_run   │
  └──────────────┘        └───────────────┘       └────────┬────────┘
                                                           │ 1:N
                                          ┌────────────────┴──────────────┐
                                          ▼                               ▼
                                 ┌─────────────────┐          ┌──────────────────┐
                                 │analysis_result  │          │ report_artifact  │
                                 │(full_result)    │          │(run_id, format)  │
                                 └─────────────────┘          └──────────────────┘

  ┌───────────┐   ┌──────────────┐   （backup_record 独立表，无外键）
  │ local_meta│   │backup_record │
  └───────────┘   └──────────────┘
```

### 关系要点

| 关系 | 基数 | 说明 |
|---|---|---|
| `warehouse` → `location` | 1:N | 库位以 `(warehouse_id, location_id)` 复合定位 |
| `sku` → `lot` / `barcode` / `supplier_sku` / `inventory_event` / `stock_snapshot` / `inventory_balance` / `inventory_event_line` / `purchase_order_line` | 1:N | `sku.sku_id` 是全库被引用最多的业务键 |
| `inventory_event` → `inventory_event` | 自引用 1:N | `reversal_of` 冲销链（可多级，撤销的撤销 = 复原） |
| `inventory_event` → `inventory_event_line` | 1:N | 多 SKU 单据扩展 |
| `inventory_event` ⇢ `inventory_balance` | **逻辑投影** | 有 `as_of_event_id` 外键标记水位，但余额可整表重建 |
| `supplier` → `purchase_order` → `purchase_order_line` | 1:N:N | 采购链路 |
| `import_batch` → `import_error` | 1:N | 错误隔离 |
| `analysis_run` → `analysis_result` / `report_artifact` | 1:N / 1:N | 分析产物链路 |

## 3.2 云端控制库 ER

```text
  ┌────────────────┐
  │ product_profile│◄─────────────┐
  │  （行业类型）   │              │ N:1 (RESTRICT)
  └───────┬────────┘              │
          │ 1:N (SET NULL)        │
          ▼                       │
  ┌────────────────┐        ┌─────┴──────┐
  │     tenant     │──1:N──►│  license   │◄──┐
  │    （商户）     │        │ （许可证）  │   │ 部分唯一
  └───┬────────┬───┘        └────────────┘   │ (仅1个ACTIVE)
      │ 1:N    │ 1:N                          │
      ▼        ▼                              │
 ┌─────────┐ ┌─────────┐              ┌───────┴────────┐
 │ account │ │ device  │              │ feature_grant  │
 └────┬────┘ └─────────┘              │  （功能授权）   │
      │ 1:N                           └────────────────┘
      ▼
 ┌─────────┐        ┌────────────┐
 │ session │        │ audit_log  │  （三列均无外键，只追加）
 └─────────┘        └────────────┘

 ┌──────────────┐  ┌──────────────┐
 │ control_meta │  │ control_enum │   （独立字典表，无外键）
 └──────────────┘  └──────────────┘
```

### 关系要点

| 关系 | 基数 | 级联 | 说明 |
|---|---|---|---|
| `tenant` → `account` | 1:N | `CASCADE` | 删除商户连带删除账号 |
| `tenant` → `device` | 1:N | `CASCADE` | |
| `tenant` → `license` | 1:N | `CASCADE` | 但同一时刻仅 1 个 `ACTIVE` |
| `tenant` → `feature_grant` | 1:N | `CASCADE` | |
| `product_profile` → `tenant` | 1:N | `SET NULL` | 行业类型下线，商户置空不删 |
| `product_profile` → `license` | 1:N | `RESTRICT` | 有许可证引用时禁止删除行业类型 |
| `account` → `session` | 1:N | `CASCADE` | |
| `session.device_id` | — | **无外键** | 可指向已删除设备或为空 |

## 3.3 跨库关系（**无数据库外键，仅逻辑关联**）

| 本地库 | 云端库 | 关联方式 |
|---|---|---|
| 无（本地库不存云端 ID） | — | 工作台通过 JWT 中的 `tenant_id` 与设备 `fingerprint` 与控制平面对齐；`analysis_run.task_id` 预留云端任务关联（M3） |

> 两套库**物理隔离、无跨库外键**，这是本地优先架构的核心约束。

---

# 第四部分：数据量与演进考虑

## 4.1 数据量估算（单商户，本地库）

| 表 | 增长驱动 | 典型量级（年） | 说明 |
|---|---|---|---|
| `inventory_event` | 每日出入库/盘点/调拨 | **10⁴ ~ 10⁶ 行** | 唯一持续高增长的事实表，性能敏感 |
| `inventory_event_line` | 多 SKU 单据 | 0 ~ 10⁵ 行 | M1 未实装写入路径 |
| `stock_snapshot` | 每日快照 × SKU × 仓 × 库位 × 批次 | 10³ ~ 10⁵ 行 | 维度最细，增速受批次/库位粒度放大 |
| `inventory_balance` | SKU × 仓 × 库位 × 批次 | 10² ~ 10⁴ 行 | **可重建**，非累积增长 |
| `sku` | 商品档案 | 10² ~ 10⁴ 行 | 低频变更 |
| `import_batch` | 导入次数 | 10¹ ~ 10³ 行 | |
| `import_error` | 导入错误行数 | 0 ~ 10⁴ 行 | 与数据质量强相关 |
| `analysis_run` / `analysis_result` | 分析次数 | 10¹ ~ 10³ 行 | `analysis_result` 单行存整份 JSON，体积较大（KB 级/次） |
| `report_artifact` | 导出次数（run × 格式） | 10¹ ~ 10³ 行 | |
| `backup_record` | 备份次数 | 10¹ ~ 10² 行 | 备份文件体积 ≈ 库体积 |

**已实测的性能基线**（`scripts/perf_bench.py`，seed=20260829，单商户）：

| 数据规模 | analyze | 全链路（build+validate+analyze+digest） | M1 阈值 |
|---|---|---|---|
| 1 万行 | 1.49 / 1.80 秒 | — | ≤ 5 秒 |
| 10 万行 | 15.80 / 12.38 秒 | 约 36 秒 | analyze ≤ 40 秒、全链路 ≤ 60 秒 |
| 100 万行 | 171.25 秒（**仅 1 次实测**） | — | **M3 冻结** |

## 4.2 演进考虑

### 4.2.1 迁移原则

- **DDL 唯一权威是 Alembic 迁移**；ORM 改动必须同步写迁移，禁止依赖 `create_all()`；
- 本地库 revision ID 用**下划线**命名（禁止 `-`，Alembic 限制）；
- 云端库 revision ID 同理（`control_0001`…）；
- 本地库迁移在**工作台启动时自动 `upgrade head`**（幂等），无需用户干预；
- 每次迁移推进 `local_meta.db_schema_version` / `control_meta.db_schema_version`；
- 备份恢复前比对 `db_schema_version` 与 `alembic_version`，**禁止静默升/降级**。

### 4.2.2 已识别的演进风险与建议

| # | 风险 | 现状 | 建议 |
|---|---|---|---|
| E-1 | `inventory_balance` 全量重建成本随事件量线性增长 | 每次事件导入成功即 `rebuild()` 全表 | 已按 `as_of_event_id` 预留增量续算水位，后续改为增量 |
| E-2 | `analysis_result.metric_json` 存整份 JSON，体积随指标数增长 | 单行 KB 级 | 若指标维度扩展到 SKU/品类级，需拆行或改列式存储 |
| E-3 | 本地库无分区/归档机制，`inventory_event` 长期增长 | 无归档 | 考虑按年度归档历史事件（需保证重放可复现） |
| E-4 | SQLite 单写入者模型 | `busy_timeout=5000` + WAL | 已满足单工作台串行写入（DECISIONS.md D-001：第一版严格单商户主工作台） |
| E-5 | 云端 `audit_log` 无清理任务 | 迁移声明保留 180 天，代码未实现 | 需补定时清理任务 |
| E-6 | 云端 `session` 表随登录/刷新增长 | 刷新轮换不新增行（同会话更新），但每次登录新增一行 | 需清理过期/已撤销会话 |
| E-7 | ORM 与迁移双写（云端库） | `models.py` 无 `server_default`/CHECK/索引/外键 `ondelete` | 建议统一：要么 ORM 补齐并启用 autogenerate，要么在 CI 中校验二者一致 |
| E-8 | 契约 Schema 演进 | `schema_version = 1.0`；新增可选字段为非破坏变更 | 删除字段/改类型/改单位必须提升 `schema_version` 并经 A、B 共同评审 |
| E-9 | 公式口径演进 | `formula_version = 0.1.0` | 任何规则变更必须提升该版本并重新生成黄金数据，说明对旧报告的影响 |
| E-10 | 分库分表 / 多商户 | 本地库单商户单文件 | 与 DECISIONS.md D-001（第一版单商户主工作台）一致；多工作台需经 G6 试点验证后立项 |

### 4.2.3 数据保留策略现状

| 数据 | 保留策略 | 是否实现 |
|---|---|---|
| 本地业务数据 | 由商户自行决定，备份文件不自动清理 | — |
| 云端审计日志 | 180 天（迁移文档串声明） | ❌ 清理任务未实现 |
| 云端会话 | 无自动清理 | ❌ |
| 同步信封密文 | TTL 30 分钟，工作台 ACK 后删除 | ⏳ M3（端点当前为 stub） |
| 备份文件 | 不自动清理 | — |

---

## 附录 A：建表语句来源

| 库 | DDL 权威位置 |
|---|---|
| 本地 SQLite | `local-data/alembic/versions/0001_meta.py` … `0006_report_backup.py`（本文字段定义已与真实运行库 `.workbench-data/warehouse.db` 的 `sqlite_master` 交叉核对） |
| 云端 PostgreSQL | `services/control-plane/alembic/versions/0001_control_meta.py` … `0004_audit.py` |

## 附录 B：校验脚本

| 脚本 | 用途 |
|---|---|
| `scripts/verify_schema.py` | 校验本地库落库结构与迁移预期一致（含 CHECK 双前缀说明） |
| `scripts/verify_backup_restore.py` | 备份-恢复全链路校验（行数守恒、业务数据可读） |
| `tests/test_migrations.py`（云端） | 临时 schema 内 upgrade head → 断言 `control-0004` → downgrade base；无 PG 时 skip |
