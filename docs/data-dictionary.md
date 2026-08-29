# 数据字典（M0 初版）

**归档目的**：关闭《项目开发文档评审报告.md》§8 修订清单第 1 项（P0-1）中"数据字典"交付物。
**范围**：M0 已落地迁移创建的 5 张表（`local_meta`、`analysis_run`、`analysis_result`、`control_meta`、`control_enum`）+ 同步信封字段（`packages/contracts-schema/sync-envelope.schema.json`）。字段定义与迁移 DDL 100% 对齐；主基线完整表组随 M1+ 迁移落地后在本字典扩展（只加不改）。
**姊妹文档**：表关系与约束图见 [er-diagram.md](er-diagram.md)；迁移结构机器校验见 `scripts/verify_schema.py`。

## 0. 全局约定

- **类型记法**：本地 SQLite 按迁移列声明（TEXT / INTEGER，SQLite 动态类型，实际存储与声明一致）；云端 PostgreSQL（TIMESTAMPTZ = `timestamptz`，TEXT，INTEGER）。
- **单位/格式**：日期 `YYYY-MM-DD`；时间 UTC ISO 8601（如 `2026-08-29T08:30:00+00:00`）；金额/数量 = Decimal 序列化字符串（如 `"123.45"`；数量 scale ≤ 3、金额 scale ≤ 2、HALF_UP，禁止 float 真值）。
- **可空**：以迁移 DDL 的 nullable 标注为准。
- **来源**：`UI` = 工作台界面/用例构造（商户操作链路）；`引擎` = warehouse-engine 输出（FakeEngine 同构）；`系统` = 迁移种子 / Alembic / 框架自动生成。
- **敏感级别**（与"本地优先"数据边界对应）：
  - `明文`：跨端明文可见的元数据（仅同步信封外层字段）；
  - `内部`：仅本地库或云端控制库内部使用，不出各自信任域；
  - `脱敏`：以密文存储/传输，或外发前必须脱敏。
- **保留期限**：本地库随商户安装存续（备份/清理由 M1+ 备份任务负责）；云端 M0 仅元数据/枚举，无业务明细。

## 1. 本地业务库（SQLite，`warehouse.db`）

### 1.1 local_meta（迁移 0001_meta）

键值型元信息表；种子随迁移写入，运行期由 Repository 只读。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| key | TEXT | 键名（见下） | 否（PK，约束名 `pk_local_meta`） | 系统 | 内部 | `db_schema_version` / `install_instance_id` / `single_primary_workbench` |
| value | TEXT | 与 key 对应的值 | 否 | 系统 | 内部 | 版本号 / UUID4 / `1`；`install_instance_id` 为安装实例标识（设备维度，非商户业务数据） |

### 1.2 analysis_run（迁移 0002_analysis_m0）

分析运行记录：`run_id` 唯一，携带引擎/公式版本与状态机。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_analysis_run`） | 系统 | 内部 | 代理主键（rowid 自增） |
| run_id | TEXT | `run-` 前缀标识 | 否（UNIQUE，`uq_analysis_run_run_id`） | UI（用例构造） | 内部 | 运行唯一标识；结果按此存取 |
| task_id | TEXT | 云端任务标识 | 是 | UI/系统（调度） | 内部 | M0 无调度，预留 |
| start_date | TEXT | `YYYY-MM-DD` | 是 | UI（请求） | 内部 | 分析期间起始（含） |
| end_date | TEXT | `YYYY-MM-DD` | 是 | UI（请求） | 内部 | 分析期间结束 |
| scope_json | TEXT | JSON 文本 | 是 | UI（请求） | 内部 | 仓库范围等 scope 快照 |
| engine_version | TEXT | 语义版本 | 否 | 引擎 | 内部 | 如 `0.1.0-fake`；结果可追溯 |
| formula_version | TEXT | 语义版本 | 否 | 引擎 | 内部 | 如 `0.1.0`；口径版本（formula-spec） |
| status | TEXT | 状态机枚举值 | 否 | 系统（状态机） | 内部 | CREATED/QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED |
| started_at | TEXT | UTC ISO 8601 | 是 | 系统 | 内部 | 运行开始时间 |
| finished_at | TEXT | UTC ISO 8601 | 是 | 系统 | 内部 | 运行结束时间 |
| error_code | TEXT | `contracts.ErrorCode` 值 | 是 | 系统/引擎 | 内部 | 失败错误码 |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

### 1.3 analysis_result（迁移 0002_analysis_m0）

分析结果行：M0 约定 `result_type="full_result"` 单行存 `AnalysisResult` 完整 JSON；`(run_id, result_type, sku_id)` 复合索引 `ix_analysis_result_run_id_result_type_sku_id`（非唯一）。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_analysis_result`） | 系统 | 内部 | 代理主键 |
| run_id | TEXT | 同 `analysis_run.run_id` | 否（FK，`fk_analysis_result_run_id_analysis_run`） | 系统（关联） | 内部 | 引用运行记录；连接启用 `foreign_keys=ON` |
| result_type | TEXT | 结果类型 | 否 | 引擎 | 内部 | M0 固定 `full_result` |
| sku_id | TEXT | SKU 标识 | 是 | 引擎（数据集） | 内部 | full_result 单行存整体结果时为空；商户业务数据仅存本地 |
| category | TEXT | 分类名 | 是 | 引擎（数据集） | 内部 | 分类维度 |
| metric_json | TEXT | JSON 文本 | 否 | 引擎 | 内部 | 指标数组序列化；金额/数量为 Decimal 字符串 |
| warning_json | TEXT | JSON 文本 | 否 | 引擎 | 内部 | Warning 数组（code/severity/message/fields/blocking 五要素） |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |

**M1 扩展说明（迁移 0003-0006 追加，M0 §1.1~1.3 原样保留）**：以下 1.4~1.21 收录 M1 新增的 17 张表，字段/约束与迁移 DDL 100% 对齐（ORM 见 `local_data/models.py`，迁移见 `local-data/alembic/versions/`）。类型与来源口径补充（不改动 §0 全局约定，仅追加）：

- **DecimalText**：ORM 类型 `local_data.models.DecimalText`，SQLite 列声明 TEXT，存 `str(Decimal)` 序列化字符串、读出还原 `Decimal` 真值（全程无 float）；金额 scale ≤ 2、数量 scale ≤ 3。表中类型列记作 `TEXT（DecimalText）`。
- **BOOLEAN**：迁移声明 `sa.Boolean`，SQLite 动态类型实际存 INTEGER（0/1）。
- **来源新增两类**（M0 §0 的 `UI`/`引擎`/`系统` 之外）：`导入` = 导入向导（`CsvImportManager`，CSV 导入链路）写入；`投影` = 余额投影服务（`local_data.projection`）按事件回放计算写入。其余沿用 M0 口径。
- **CHECK 约束只在迁移内定义**（迁移是 DDL 唯一权威，模型层不重复声明）；DecimalText 列的非负/正数 CHECK 用 `CAST(x AS REAL)` 转数值比较（NULL 转换后仍为 NULL，放行）。
- 敏感级别：17 表全部为商户业务数据，只落本地库（§0 的 `内部` 级），永不上云端（对齐 §4 第 2 条边界）。

### 1.4 sku（迁移 0003_master_data）

SKU 主数据：`sku_id` 唯一；`category` / `is_active` 建索引（ABC 分层与有效过滤查询）。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_sku`） | 系统 | 内部 | 代理主键（rowid 自增） |
| sku_id | TEXT | SKU 标识 | 否（UNIQUE，`uq_sku_sku_id`） | 导入 | 内部 | SKU 唯一标识；重复写入抛唯一约束错误 |
| name | TEXT | 名称 | 否 | 导入 | 内部 | SKU 显示名 |
| category | TEXT | 品类 | 是 | 导入 | 内部 | 分类维度；索引 `ix_sku_category` |
| sub_category | TEXT | 子品类 | 是 | 导入 | 内部 | 二级分类 |
| unit | TEXT | 计量单位名（瓶/箱等） | 是 | 导入 | 内部 | 展示单位 |
| unit_scale | TEXT（DecimalText） | 换算倍率（数量） | 是 | 导入 | 内部 | 包装 → 基础单位换算倍率 |
| unit_cost | TEXT（DecimalText） | 金额，scale ≤ 2 | 是 | 导入 | 内部 | 单位成本（移动加权平均口径输入） |
| industry | TEXT | 行业 | 是 | 导入 | 内部 | 行业维度 |
| is_active | BOOLEAN | 0/1 | 否 | 导入 | 内部 | 有效标志；索引 `ix_sku_is_active`；喂引擎时不过滤（期间事件可能引用已停用 SKU，保引用完整） |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

### 1.5 barcode（迁移 0003_master_data）

条码映射：`barcode` 唯一；一条条码至多映射一个有效 SKU（唯一约束 + `is_active` 过滤在 Repository 层共同保证）。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_barcode`） | 系统 | 内部 | 代理主键 |
| barcode | TEXT | 条码值 | 否（UNIQUE，`uq_barcode_barcode`） | 导入 | 内部 | 条码全局唯一 |
| sku_id | TEXT | SKU 标识 | 否（FK，`fk_barcode_sku_id_sku` → `sku.sku_id`） | 导入 | 内部 | 引用 SKU；连接启用 `foreign_keys=ON` |
| package_unit | TEXT | 包装单位名 | 是 | 导入 | 内部 | 如 箱/中包 |
| conversion_factor | TEXT（DecimalText） | 换算系数（数量） | 是 | 导入 | 内部 | 条码包装单位 → 基础单位换算 |
| is_active | BOOLEAN | 0/1 | 否 | 导入 | 内部 | 有效标志 |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

### 1.6 warehouse（迁移 0003_master_data）

仓库主数据：`warehouse_id` 唯一。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_warehouse`） | 系统 | 内部 | 代理主键 |
| warehouse_id | TEXT | 仓库标识 | 否（UNIQUE，`uq_warehouse_warehouse_id`） | 导入 | 内部 | 仓库唯一标识 |
| name | TEXT | 仓库名 | 否 | 导入 | 内部 | 显示名 |
| address | TEXT | 地址 | 是 | 导入 | 内部 | 自由文本 |
| is_active | BOOLEAN | 0/1 | 否 | 导入 | 内部 | 有效标志；默认分析请求取全部有效仓 |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

### 1.7 location（迁移 0003_master_data）

库位主数据：`(warehouse_id, location_id)` 唯一（`location_id` 单列不唯一）。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_location`） | 系统 | 内部 | 代理主键 |
| warehouse_id | TEXT | 仓库标识 | 否（FK，`fk_location_warehouse_id_warehouse` → `warehouse.warehouse_id`） | 导入 | 内部 | 所属仓库 |
| location_id | TEXT | 库位标识 | 否 | 导入 | 内部 | 仓内库位编号 |
| name | TEXT | 库位名 | 是 | 导入 | 内部 | 显示名 |
| is_active | BOOLEAN | 0/1 | 否 | 导入 | 内部 | 有效标志 |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

UNIQUE 约束 `uq_location_warehouse_id_location_id`：`(warehouse_id, location_id)`。

### 1.8 supplier（迁移 0003_master_data）

供应商主数据：`supplier_id` 唯一。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_supplier`） | 系统 | 内部 | 代理主键 |
| supplier_id | TEXT | 供应商标识 | 否（UNIQUE，`uq_supplier_supplier_id`） | 导入 | 内部 | 供应商唯一标识 |
| name | TEXT | 供应商名 | 否 | 导入 | 内部 | 显示名 |
| contact | TEXT | 联系方式 | 是 | 导入 | 内部 | 自由文本；仅本地库内部使用，不出信任域 |
| is_active | BOOLEAN | 0/1 | 否 | 导入 | 内部 | 有效标志 |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

### 1.9 supplier_sku（迁移 0003_master_data）

供应商-SKU 补货参数：`(supplier_id, sku_id)` 组合主键；5 个数量/金额参数非负 CHECK（业务语义随 B 侧 replenishment 落地，M1 只建结构）。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| supplier_id | TEXT | 供应商标识 | 否（PK 组合列，`pk_supplier_sku`；FK `fk_supplier_sku_supplier_id_supplier` → `supplier.supplier_id`） | 导入 | 内部 | 组合主键之一 |
| sku_id | TEXT | SKU 标识 | 否（PK 组合列；FK `fk_supplier_sku_sku_id_sku` → `sku.sku_id`） | 导入 | 内部 | 组合主键之二 |
| lead_time_days | INTEGER | 天 | 是 | 导入 | 内部 | 供货周期；CHECK `ck_supplier_sku_lead_time_nonneg`（≥ 0） |
| moq | TEXT（DecimalText） | 数量 | 是 | 导入 | 内部 | 最小起订量；CHECK `ck_supplier_sku_moq_nonneg` |
| pack_size | TEXT（DecimalText） | 数量 | 是 | 导入 | 内部 | 包装规格；CHECK `ck_supplier_sku_pack_size_nonneg` |
| order_cost | TEXT（DecimalText） | 金额，scale ≤ 2 | 是 | 导入 | 内部 | 单次订货成本；CHECK `ck_supplier_sku_order_cost_nonneg` |
| holding_cost | TEXT（DecimalText） | 金额，scale ≤ 2 | 是 | 导入 | 内部 | 库存持有成本；CHECK `ck_supplier_sku_holding_cost_nonneg` |
| is_preferred | BOOLEAN | 0/1 | 否 | 导入 | 内部 | 首选供应商标志（默认 0） |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

### 1.10 lot（迁移 0003_master_data）

批次主数据：`(sku_id, lot_id)` 唯一；有效期不得早于生产日期（`ck_lot_expiry_not_before_production`；任一为 NULL 时比较结果为 NULL，CHECK 放行）。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_lot`） | 系统 | 内部 | 代理主键 |
| lot_id | TEXT | 批次标识 | 否 | 导入 | 内部 | 批次号（SKU 内唯一） |
| sku_id | TEXT | SKU 标识 | 否（FK，`fk_lot_sku_id_sku` → `sku.sku_id`） | 导入 | 内部 | 所属 SKU |
| production_date | TEXT | `YYYY-MM-DD` | 是 | 导入 | 内部 | 生产日期（ISO 日期文本字典序即时间序） |
| expiry_date | TEXT | `YYYY-MM-DD` | 是 | 导入 | 内部 | 到期日期；`expiry_date >= production_date`（CHECK） |
| received_at | TEXT | UTC ISO 8601 | 是 | 导入 | 内部 | 收货时间 |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

UNIQUE 约束 `uq_lot_sku_id_lot_id`：`(sku_id, lot_id)`。

### 1.11 inventory_event（迁移 0004_inventory_events）

库存事件：唯一库存事实来源（事件先落库为真）；`event_id` 唯一保证幂等导入；数量必须大于 0，方向由 `move_type` 承载。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_inventory_event`） | 系统 | 内部 | 代理主键 |
| event_id | TEXT | 事件标识 | 否（UNIQUE，`uq_inventory_event_event_id`） | 导入 | 内部 | 事件唯一标识；重复 `event_id` 幂等跳过（upsert） |
| sku_id | TEXT | SKU 标识 | 否（FK，`fk_inventory_event_sku_id_sku` → `sku.sku_id`） | 导入 | 内部 | 事件 SKU |
| warehouse_id | TEXT | 仓库标识 | 否（FK，`fk_inventory_event_warehouse_id_warehouse` → `warehouse.warehouse_id`） | 导入 | 内部 | 事件仓库 |
| location_id | TEXT | 库位标识 | 是 | 导入 | 内部 | 可空维度；复合 FK `fk_inventory_event_warehouse_id_location`：`(warehouse_id, location_id)` → `location(warehouse_id, location_id)` |
| lot_id | TEXT | 批次标识 | 是 | 导入 | 内部 | 可空维度；复合 FK `fk_inventory_event_sku_id_lot`：`(sku_id, lot_id)` → `lot(sku_id, lot_id)` |
| move_type | TEXT | 枚举 | 否 | 导入 | 内部 | 取值见 §1.21（本地库 8 值）；未知类型投影时记 Warning 跳过 |
| quantity | TEXT（DecimalText） | 数量，恒为正 | 否 | 导入 | 内部 | CHECK `ck_inventory_event_quantity_positive`（`CAST(quantity AS REAL) > 0`） |
| unit_cost | TEXT（DecimalText） | 金额，scale ≤ 2 | 是 | 导入 | 内部 | 事件单位成本（COGS 输入） |
| occurred_at | TEXT | UTC ISO 8601 | 否 | 导入 | 内部 | 发生时间；回放/期间过滤的排序与闭区间基准 |
| source | TEXT | 来源标识 | 否 | 导入 | 内部 | 如 `IMPORT_CSV` / `UI`；喂引擎时映射为契约 `EventSource`（见 1.11 附注） |
| source_ref | TEXT | 源单据号 | 是 | 导入 | 内部 | 源单据回溯信息 |
| reversal_of | TEXT | 事件标识 | 是（自引用 FK，`fk_inventory_event_reversal_of_inventory_event` → `inventory_event.event_id`） | 导入 | 内部 | 冲销指向的原事件 |
| created_by | TEXT | 操作者 | 是 | 导入 | 内部 | 创建人标识 |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

索引 `ix_inventory_event_sku_id_warehouse_id_occurred_at`：`(sku_id, warehouse_id, occurred_at)`（期间明细查询）。

**source → 契约 `EventSource` 映射**（`SqliteDatasetAdapter`）：`IMPORT`/`IMPORT_CSV` → IMPORT、`UI`/`DESKTOP` → DESKTOP、`MINI_PROGRAM` → MINI_PROGRAM、`ADJUSTMENT` → ADJUSTMENT；未知取值回退 IMPORT（不因未知值中断构造）。

### 1.12 inventory_event_line（迁移 0004_inventory_events）

库存事件行（多 SKU 单据的可扩展结构）：`(event_id, line_no)` 唯一。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_inventory_event_line`） | 系统 | 内部 | 代理主键 |
| event_id | TEXT | 事件标识 | 否（FK，`fk_inventory_event_line_event_id_inventory_event` → `inventory_event.event_id`） | 导入 | 内部 | 所属事件 |
| line_no | INTEGER | 行号 | 否 | 导入 | 内部 | 事件内行号 |
| sku_id | TEXT | SKU 标识 | 否（FK，`fk_inventory_event_line_sku_id_sku` → `sku.sku_id`） | 导入 | 内部 | 行 SKU |
| quantity | TEXT（DecimalText） | 数量 | 否 | 导入 | 内部 | 行数量（行级无 CHECK，约束在事件级） |
| unit_cost | TEXT（DecimalText） | 金额，scale ≤ 2 | 是 | 导入 | 内部 | 行单位成本 |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

UNIQUE 约束 `uq_inventory_event_line_event_id_line_no`：`(event_id, line_no)`。

### 1.13 stock_snapshot（迁移 0004_inventory_events）

库存快照（期初/期末余额口径）：五元组 `(snapshot_date, sku_id, warehouse_id, location_id, lot_id)` 唯一；`snapshot_id` 单列唯一。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_stock_snapshot`） | 系统 | 内部 | 代理主键 |
| snapshot_id | TEXT | 快照标识 | 否（UNIQUE，`uq_stock_snapshot_snapshot_id`） | 导入 | 内部 | 快照行唯一标识 |
| snapshot_date | TEXT | `YYYY-MM-DD` | 否 | 导入 | 内部 | 快照日；期间过滤闭区间基准 |
| sku_id | TEXT | SKU 标识 | 否（FK，`fk_stock_snapshot_sku_id_sku` → `sku.sku_id`） | 导入 | 内部 | 快照 SKU |
| warehouse_id | TEXT | 仓库标识 | 否（FK，`fk_stock_snapshot_warehouse_id_warehouse` → `warehouse.warehouse_id`） | 导入 | 内部 | 快照仓库 |
| location_id | TEXT | 库位标识 | 是 | 导入 | 内部 | 可空维度；复合 FK `fk_stock_snapshot_warehouse_id_location` |
| lot_id | TEXT | 批次标识 | 是 | 导入 | 内部 | 可空维度；复合 FK `fk_stock_snapshot_sku_id_lot` |
| quantity | TEXT（DecimalText） | 数量 | 否 | 导入 | 内部 | 时点数量 |
| inventory_value | TEXT（DecimalText） | 金额，scale ≤ 2 | 是 | 导入 | 内部 | 时点库存价值 |
| source | TEXT | 来源标识 | 是 | 导入 | 内部 | 如 `IMPORT_CSV` |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

UNIQUE 约束 `uq_stock_snapshot_date_sku_wh_loc_lot`：五元组（快照五元组，一行一个维度组合时点）。

### 1.14 purchase_order（迁移 0004_inventory_events）

采购订单（M1 只建结构，业务逻辑随 B 侧 replenishment 落地）：`po_id` 唯一；状态枚举 CHECK。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_purchase_order`） | 系统 | 内部 | 代理主键 |
| po_id | TEXT | 订单标识 | 否（UNIQUE，`uq_purchase_order_po_id`） | 导入 | 内部 | 订单唯一标识 |
| supplier_id | TEXT | 供应商标识 | 否（FK，`fk_purchase_order_supplier_id_supplier` → `supplier.supplier_id`） | 导入 | 内部 | 供应商 |
| status | TEXT | 枚举 | 否 | 导入 | 内部 | CHECK `ck_purchase_order_status`（DRAFT/ORDERED/RECEIVED/CLOSED/CANCELLED，见 §1.21） |
| ordered_at | TEXT | UTC ISO 8601 | 是 | 导入 | 内部 | 下单时间 |
| expected_at | TEXT | UTC ISO 8601 | 是 | 导入 | 内部 | 预计到货时间 |
| closed_at | TEXT | UTC ISO 8601 | 是 | 导入 | 内部 | 关闭时间 |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

### 1.15 purchase_order_line（迁移 0004_inventory_events）

采购订单行：`(po_id, line_no)` 唯一；数量非负 CHECK（超收容差属 replenishment 业务口径，M1 只做非负下界）。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_purchase_order_line`） | 系统 | 内部 | 代理主键 |
| po_id | TEXT | 订单标识 | 否（FK，`fk_purchase_order_line_po_id_purchase_order` → `purchase_order.po_id`） | 导入 | 内部 | 所属订单 |
| line_no | INTEGER | 行号 | 否 | 导入 | 内部 | 订单内行号 |
| sku_id | TEXT | SKU 标识 | 否（FK，`fk_purchase_order_line_sku_id_sku` → `sku.sku_id`） | 导入 | 内部 | 行 SKU |
| ordered_qty | TEXT（DecimalText） | 数量 | 否 | 导入 | 内部 | 订购数；CHECK `ck_purchase_order_line_ordered_qty_nonneg`（≥ 0） |
| received_qty | TEXT（DecimalText） | 数量 | 否 | 导入 | 内部 | 已收数；CHECK `ck_purchase_order_line_received_qty_nonneg`（≥ 0） |
| unit_cost | TEXT（DecimalText） | 金额，scale ≤ 2 | 是 | 导入 | 内部 | 行单位成本 |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

UNIQUE 约束 `uq_purchase_order_line_po_id_line_no`：`(po_id, line_no)`。

### 1.16 inventory_balance（迁移 0004_inventory_events）

库存余额：**可整表重建的查询投影**，非唯一事实来源（`inventory_event` 才是事实）。身份维度 `(sku_id, warehouse_id, location_id, lot_id)` 不设业务 UNIQUE（SQLite 组合唯一对 NULL 维度不生效）；重建口径 = 清空后按 `occurred_at`（`(occurred_at, event_id)` 稳定排序）回放事件全量重写，不保存不可再生状态。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_inventory_balance`） | 系统 | 内部 | 代理主键 |
| sku_id | TEXT | SKU 标识 | 否（FK，`fk_inventory_balance_sku_id_sku` → `sku.sku_id`） | 投影 | 内部 | 维度之一 |
| warehouse_id | TEXT | 仓库标识 | 否（FK，`fk_inventory_balance_warehouse_id_warehouse` → `warehouse.warehouse_id`） | 投影 | 内部 | 维度之二 |
| location_id | TEXT | 库位标识 | 是 | 投影 | 内部 | 维度之三（NULL 维度单独成组）；复合 FK `fk_inventory_balance_warehouse_id_location` |
| lot_id | TEXT | 批次标识 | 是 | 投影 | 内部 | 维度之四；复合 FK `fk_inventory_balance_sku_id_lot` |
| on_hand_qty | TEXT（DecimalText） | 数量 | 否 | 投影 | 内部 | 账面在手量 = 按 `occurred_at` 顺序回放事件的结果（方向语义见 §1.21） |
| available_qty | TEXT（DecimalText） | 数量 | 否 | 投影 | 内部 | M1 口径 = `on_hand_qty`（无预留概念） |
| reserved_qty | TEXT（DecimalText） | 数量 | 否 | 投影 | 内部 | M1 恒 0（预留随后续里程碑） |
| as_of_event_id | TEXT | 事件标识 | 是（FK，`fk_inventory_balance_as_of_event_id_inventory_event` → `inventory_event.event_id`） | 投影 | 内部 | 该维度最后回放到的事件 |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

### 1.17 import_batch（迁移 0005_import）

导入批次：`batch_id` 唯一；状态枚举 CHECK；`file_hash` 建索引（重复文件导入提示据此检出，不静默重复入库）。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_import_batch`） | 系统 | 内部 | 代理主键 |
| batch_id | TEXT | 批次标识 | 否（UNIQUE，`uq_import_batch_batch_id`） | 导入 | 内部 | 批次唯一标识 |
| file_name | TEXT | 文件名 | 否 | 导入 | 内部 | 原始文件名 |
| file_hash | TEXT | SHA-256 十六进制 | 否 | 导入 | 内部 | 文件内容哈希；索引 `ix_import_batch_file_hash` |
| source_type | TEXT | 来源类型 | 否 | 导入 | 内部 | 如 `CSV`（xlsx 随 M2） |
| started_at | TEXT | UTC ISO 8601 | 否 | 导入 | 内部 | 批次开始时间 |
| completed_at | TEXT | UTC ISO 8601 | 是 | 导入 | 内部 | 批次完成/失败时间 |
| status | TEXT | 枚举 | 否 | 导入 | 内部 | CHECK `ck_import_batch_status`（RUNNING/COMPLETED/FAILED，见 §1.21） |
| row_count | INTEGER | 行数 | 否 | 导入 | 内部 | 提交总行数（合法 + 错误） |
| error_count | INTEGER | 行数 | 否 | 导入 | 内部 | 隔离进 `import_error` 的行数 |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

### 1.18 import_error（迁移 0005_import）

导入错误行（错误隔离）：校验失败的行按 `(batch_id, row_no)` 定位隔离，不阻塞合法行入库；`raw_value` / `suggestion` 支撑错误修复闭环（敏感值只落业务库，禁入技术日志）。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_import_error`） | 系统 | 内部 | 代理主键 |
| batch_id | TEXT | 批次标识 | 否（FK，`fk_import_error_batch_id_import_batch` → `import_batch.batch_id`） | 导入 | 内部 | 所属批次 |
| row_no | INTEGER | 行号 | 否 | 导入 | 内部 | 源文件行号（定位错误行） |
| field_name | TEXT | 字段名 | 是 | 导入 | 内部 | 出错字段（定位到字段） |
| error_code | TEXT | 错误码 | 否 | 导入 | 内部 | 如 数量为负 / SKU 不存在等校验错误码 |
| raw_value | TEXT | 原始值 | 是 | 导入 | 内部 | 出错字段的原始文本（修复闭环依据） |
| suggestion | TEXT | 修复建议 | 是 | 导入 | 内部 | 修复建议文本 |
| resolved_at | TEXT | UTC ISO 8601 | 是 | UI | 内部 | 错误修复闭环时间（未修复为空） |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

索引 `ix_import_error_batch_id_row_no`：`(batch_id, row_no)`（按行定位）。

### 1.19 report_artifact（迁移 0006_report_backup）

报告产物登记：`(run_id, format)` 唯一（同一 run 同一格式只保留一条记录，重复导出 = 重新生成并更新记录）；报告与 `run_id` 一一对应可追溯。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_report_artifact`） | 系统 | 内部 | 代理主键 |
| report_id | TEXT | 报告标识 | 否（UNIQUE，`uq_report_artifact_report_id`） | 系统 | 内部 | 一次导出产物的唯一标识 |
| run_id | TEXT | 运行标识 | 否（FK，`fk_report_artifact_run_id_analysis_run` → `analysis_run.run_id`） | 系统（关联） | 内部 | 引用分析运行 |
| format | TEXT | 枚举 | 否 | 系统 | 内部 | `HTML` / `CSV`（应用层受控词表，见 §1.21） |
| file_path | TEXT | 产物绝对路径 | 否 | 系统 | 内部 | 报告文件位置（数据目录同级 `reports/`） |
| sha256 | TEXT | SHA-256 十六进制 | 否 | 系统 | 内部 | 产物内容校验值 |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新（重复导出刷新） |

UNIQUE 约束 `uq_report_artifact_run_id_format`：`(run_id, format)`。

### 1.20 backup_record（迁移 0006_report_backup）

备份登记：`backup_id` 唯一；类型/状态枚举 CHECK；`db_schema_version` 记录备份时本地库 schema 版本（恢复校验依据）。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK，`pk_backup_record`） | 系统 | 内部 | 代理主键 |
| backup_id | TEXT | 备份标识 | 否（UNIQUE，`uq_backup_record_backup_id`） | 系统 | 内部 | 备份唯一标识 |
| file_path | TEXT | 备份文件路径 | 否 | 系统 | 内部 | VACUUM INTO 产物位置（数据目录同级 `backups/`） |
| backup_type | TEXT | 枚举 | 否 | 系统 | 内部 | CHECK `ck_backup_record_backup_type`（MANUAL/AUTO，见 §1.21） |
| db_schema_version | TEXT | 如 `local-0006` | 否 | 系统 | 内部 | 备份时 `local_meta.db_schema_version`（恢复校验比对） |
| sha256 | TEXT | SHA-256 十六进制 | 否 | 系统 | 内部 | 备份文件校验值 |
| size_bytes | INTEGER | 字节 | 否 | 系统 | 内部 | 备份文件大小 |
| status | TEXT | 枚举 | 否 | 系统 | 内部 | CHECK `ck_backup_record_status`（CREATED/VERIFIED/FAILED，见 §1.21） |
| verified_at | TEXT | UTC ISO 8601 | 是 | 系统 | 内部 | 读回复验时间；VERIFIED 时必填（§35.4 应用层保证） |
| created_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认 `utc_now_iso()` |
| updated_at | TEXT | UTC ISO 8601 | 否 | 系统 | 内部 | 默认同上，`onupdate` 刷新 |

### 1.21 值域与状态枚举（M1 迁移 CHECK 与应用层词表）

| 表.字段 | 取值集合 | 约束方式 | 说明 |
|---|---|---|---|
| `inventory_event.move_type` | `INBOUND` / `OUTBOUND` / `RETURN` / `SCRAP` / `ADJUSTMENT` / `TRANSFER_IN` / `TRANSFER_OUT` / `REVERSAL`（8 值，`local_data.models.MOVE_TYPES`） | 应用层受控词表（迁移无 CHECK；未知类型投影时记 Warning 跳过，不阻断） | 数量恒为正、方向由类型承载；本地扩展与契约映射见下表 |
| `import_batch.status` | `RUNNING` / `COMPLETED` / `FAILED` | 迁移 CHECK `ck_import_batch_status` | 批次状态机：RUNNING → COMPLETED / FAILED，未知状态不允许落库 |
| `purchase_order.status` | `DRAFT` / `ORDERED` / `RECEIVED` / `CLOSED` / `CANCELLED` | 迁移 CHECK `ck_purchase_order_status` | 业务语义随 B 侧 replenishment 落地，M1 先约束取值域 |
| `report_artifact.format` | `HTML` / `CSV`（`local_data.models.REPORT_FORMATS`） | 应用层受控词表 | 导出器支持的报告格式 |
| `backup_record.backup_type` | `MANUAL` / `AUTO` | 迁移 CHECK `ck_backup_record_backup_type` | 手动备份 / 自动备份（每日自动、恢复前安全备份） |
| `backup_record.status` | `CREATED` / `VERIFIED` / `FAILED` | 迁移 CHECK `ck_backup_record_status` | 状态机：CREATED（文件已生成）→ VERIFIED（读回复验通过，`verified_at` 必填）/ FAILED |

**`inventory_event.move_type` 与契约 `MoveType` 的映射**（本地库在契约 7 值之外扩展了带方向的调拨与本地盘点口径，映射发生在 `SqliteDatasetAdapter`，云端 `control_enum` 的 `move_type` 种子仍取契约 7 值）：

| 本地库取值 | 回放方向（`local_data.projection`） | 契约 `MoveType` | 口径说明 |
|---|---|---|---|
| `INBOUND` | +q | `INBOUND` | 入库 |
| `OUTBOUND` | −q | `OUTBOUND` | 出库 |
| `RETURN` | +q | `RETURN` | 退货入库 |
| `SCRAP` | −q | `SCRAP` | 报废 |
| `ADJUSTMENT` | 差额（实盘数 − 当前账面，可加可减） | `STOCKTAKE` | 本地盘点：quantity 记实盘数按差额调整；契约 STOCKTAKE 同为盘点口径 |
| `TRANSFER_IN` | +q | `TRANSFER` | 调拨入仓（M1 单仓口径：调拨在同表内拆为出/入两条事件，不再建跨仓结构；契约只有无方向的 TRANSFER） |
| `TRANSFER_OUT` | −q | `TRANSFER` | 调拨出仓（同上，与 `TRANSFER_IN` 成对） |
| `REVERSAL` | 反转 `reversal_of` 原事件方向 | `REVERSAL` | 冲销：按原事件数量取反方向；原事件为 `ADJUSTMENT` 的冲销 M1 不支持（以反向盘点表达），投影记 Warning 跳过 |

## 2. 云端控制库（PostgreSQL）

### 2.1 control_meta（迁移 0001_control_meta，revision `control_0001`）

数据库版本与元信息键值表。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| key | TEXT | 键名 | 否（PK） | 系统（迁移种子） | 内部 | `db_schema_version` / `control_plane_version` |
| value | TEXT | 与 key 对应的值 | 否 | 系统 | 内部 | `control-0001` / `0.1.0` |
| created_at | TIMESTAMPTZ | UTC 时间戳 | 否 | 系统 | 内部 | `server_default now()` |
| updated_at | TIMESTAMPTZ | UTC 时间戳 | 否 | 系统 | 内部 | `server_default now()`；应用层更新时刷新 |

### 2.2 control_enum（迁移 0001_control_meta，revision `control_0001`）

基础枚举字典（task_status / run_status / device_status / sync_status / move_type），种子 36 行随迁移 bulk_insert。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| id | INTEGER | 自增序号 | 否（PK） | 系统 | 内部 | 代理主键 |
| code | TEXT | `<kind>:<value>` | 否（UNIQUE） | 系统（迁移种子） | 内部 | 全局唯一；不同 kind 枚举值可能重名（如两个 CREATED） |
| kind | TEXT | 枚举类别 | 否 | 系统 | 内部 | 五类，取值集合见 er-diagram.md §2.1 |

## 3. 同步信封字段（sync-envelope.schema.json）

**Schema**：`packages/contracts-schema/sync-envelope.schema.json`（draft 2020-12，`$id` 指向仓库相对路径）。
**语义**：小程序同步事件信封——云端下发至工作台的加密事件外层。云端只校验商户、目标设备、大小、时间和幂等键，不解密业务内容；工作台解密后本地校验 `sku_id`、数量、仓库、库位和批次（业务内容不出商户本地库）。
**约束总则**：`additionalProperties: false`；下表 9 个字段全部必填（`required`），无默认值；正反例测试见 `tests/contract/test_sync_envelope.py`。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| event_id | string | `^evt_[0-9A-Za-z_-]+$` | 否 | 系统（云端事件） | 明文 | 事件唯一标识 |
| merchant_id | string | `^mch_[0-9A-Za-z_-]+$` | 否 | 系统（云端） | 明文 | 商户标识 |
| target_device_id | string | `^dev_[0-9A-Za-z_-]+$` | 否 | 系统（云端） | 明文 | 目标工作台设备标识 |
| idempotency_key | string | `^[0-9A-Za-z_-]{8,128}$` | 否 | UI（小程序请求侧生成） | 明文 | 幂等键，云端去重 |
| algorithm | string | const `AES-256-GCM` | 否 | 系统 | 明文 | 加密算法（冻结值，唯一合法取值） |
| nonce | string | base64url `^[A-Za-z0-9_-]+$`，非空 | 否 | 系统 | 明文 | AES-GCM 随机数（本身非机密） |
| ciphertext | string | base64url `^[A-Za-z0-9_-]+$`，非空 | 否 | 系统（加密产物） | 脱敏（密文） | 加密后的业务载荷；密钥不下发云端校验路径 |
| created_at | string | RFC 3339 date-time（UTC） | 否 | 系统 | 明文 | 信封创建时间 |
| expires_at | string | RFC 3339 date-time（UTC） | 否 | 系统 | 明文 | 过期时间；`created_at < expires_at` 为应用层校验（draft 2020-12 无法表达跨字段比较），由 `validate_envelope_timing` 覆盖 |

## 4. 与主基线完整表组的差异（M0 占位声明）

1. `analysis_run` / `analysis_result` 为 M0 持久化占位（0002_analysis_m0）：主基线规定完整字段在 0005_analysis 落地，届时"只加不改"（仅新增字段/索引，不修改既有列）。
2. 云端 M0 不建商户业务明细表（`sku`、`movement`、`unit_cost` 等永不建于云端，降低敏感数据集中化风险）。
3. 本地 `inventory_event` / `inventory_balance` 等表组随 M1 导入闭环迁移落地，届时本字典与 er-diagram.md 同步扩展。

## 5. 版本记录

| 版本 | 日期 | 变更摘要 | 迁移（revision） |
|---|---|---|---|
| M0 初版 | 2026-08-29 | 首版：本地 3 表 + 云端 2 表 + 同步信封字段；§4 占位声明（本地业务表组随 M1 落地） | `0001_meta`、`0002_analysis_m0`、`control_0001` |
| M1 扩展 | 2026-08-29 | 兑现 §4 第 3 条占位声明：新增本地业务库 17 表（§1.4~1.20：主数据 7 + 事件 6 + 导入 2 + 报告备份 2）与值域/状态枚举（§1.21）；M0 段落原样保留（只加不改） | `0003_master_data`、`0004_inventory_events`、`0005_import`、`0006_report_backup` |
