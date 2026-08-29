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

## 2. 云端控制库（PostgreSQL）

### 2.1 control_meta（迁移 0001_control_meta，revision `control-0001`）

数据库版本与元信息键值表。

| 字段 | 类型 | 单位/格式 | 可空 | 来源 | 敏感级别 | 说明 |
|---|---|---|---|---|---|---|
| key | TEXT | 键名 | 否（PK） | 系统（迁移种子） | 内部 | `db_schema_version` / `control_plane_version` |
| value | TEXT | 与 key 对应的值 | 否 | 系统 | 内部 | `control-0001` / `0.1.0` |
| created_at | TIMESTAMPTZ | UTC 时间戳 | 否 | 系统 | 内部 | `server_default now()` |
| updated_at | TIMESTAMPTZ | UTC 时间戳 | 否 | 系统 | 内部 | `server_default now()`；应用层更新时刷新 |

### 2.2 control_enum（迁移 0001_control_meta，revision `control-0001`）

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
