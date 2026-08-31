# M1 本地业务闭环（开发者 A）Spec

## Why

M0 已交付平台骨架（FakeEngine 全链路、双库迁移框架、CI 门禁），但工作台的数据来源仍是 fixture，本地库只有元数据与分析结果表。M1 要打通"导入真实数据 → 本地持久化 → 分析 → 报告 → 备份"的完整本地业务闭环，让商户业务数据真正落进本地 SQLite（本地优先原则的第一步实装）。

## What Changes

- **新增本地迁移**（沿用 alembic revision 链，revision ID 一律下划线命名，禁止 `-`）：
  - `0003_master_data`：主数据组 7 表（sku、barcode、warehouse、location、supplier、supplier_sku、lot）
  - `0004_inventory_events`：事件组 6 表（inventory_event、inventory_event_line、stock_snapshot、purchase_order、purchase_order_line、inventory_balance）
  - `0005_import`：导入治理 2 表（import_batch、import_error）
  - `0006_report_backup`：报告与备份 2 表（report_artifact、backup_record）
  - 迁移编号相对主基线 §35.7 顺延（M0 已占 0002_analysis_m0），表结构字段以 §35.4 为准
- **local-data 扩展**：新增主数据/事件/导入/报告/备份的 ORM 模型与 Repository（含余额投影重建逻辑）
- **导入向导（PySide6）**：文件选择 → 字段映射 → 预览 → 校验 → 提交 → 错误修复闭环；支持 CSV（Excel/`openpyxl` 随 M2 按需）；错误行隔离进 `import_error`，不阻塞合法行（错误隔离原则）
- **EngineDataset 适配器**：从本地库读取 sku/事件/快照构造 `EngineDataset`，先接 FakeEngine 跑通（B 的真实 wheel 就绪后无缝替换）
- **分析闭环增强**：分析用例数据源从 fixture 切换到本地库；历史运行列表 + 按 run_id 重查
- **报告导出**：HTML 与 CSV 两种格式，产物登记 `report_artifact`（run_id + format 唯一，SHA-256 记录）
- **备份与恢复**：`backup.py`（VACUUM INTO 全量备份 + SHA-256 校验 + backup_record 登记）、`restore`（恢复到临时库校验后切换，失败不覆盖当前库）、`verify_backup_restore.py` 脚本
- **CI**：platform-ci 增量覆盖新测试；全量测试无回归（B 的 47 + A 的 M0 114 基线）

### 明确不做（留给后续里程碑）

- 托盘 Agent、同步（M3）；登录认证、Web 管理页实装（M2）；真实引擎 calculators（B 的 M1）
- 采购订单/补货参数的 UI 与导入（表结构先建，业务逻辑随 B 的 replenishment 落地）
- Excel/xlsx 导入（M2 按需；M1 只做 CSV，编码自动探测 UTF-8/GBK）

## Impact

- Affected specs: `deliver-m0-platform-baseline`（在其骨架上扩展，不改已有表——"只加不改"）
- Affected code:
  - `local-data/alembic/versions/`（4 个新迁移）、`local-data/src/local_data/models.py`、`repository.py`
  - `apps/workbench-desktop/app/`（presentation 导入向导 + 报告页、application 导入/分析用例、infrastructure dataset_adapter/backup）
  - `scripts/verify_schema.py`、`docs/data-dictionary.md`、`docs/er-diagram.md`、`docs/m1-handover-a.md`
- 契约不变：`contracts` 包零改动（EngineDataset/AnalysisResult 已在 1.0 冻结）；若发现缺口提 Issue 给 B，不单方修改

## ADDED Requirements

### Requirement: 主数据持久化

系统 SHALL 提供主数据组 7 表的迁移、ORM 模型与 Repository，字段与约束对齐主基线 §35.4（`sku_id UNIQUE`、条码唯一映射有效 SKU、`expiry_date >= production_date` 等）。

#### Scenario: SKU 存取往返

- **WHEN** Repository 写入一个 SKU 并按 `sku_id` 查询
- **THEN** 返回的字段与写入值一致；重复 `sku_id` 写入抛出唯一约束错误

### Requirement: 库存事件与余额投影

系统 SHALL 以 `inventory_event` 为唯一库存事实来源（`event_id UNIQUE` 幂等、数量必须大于 0），`inventory_balance` 为可重建投影；事件先落库，再触发投影更新。

#### Scenario: 事件追加与余额重建

- **WHEN** 导入一批入库/出库事件后执行余额重建
- **THEN** `inventory_balance.on_hand_qty` 等于按 `occurred_at` 顺序回放事件的结果；重复 `event_id` 被幂等跳过并计入跳过数

#### Scenario: 余额可重建

- **WHEN** 清空 `inventory_balance` 后再次重建
- **THEN** 结果与重建前一致（投影不保存不可再生的状态）

### Requirement: 导入向导与错误隔离

系统 SHALL 提供 CSV 导入向导（文件选择 → 字段映射 → 预览 → 校验 → 提交），校验失败的行写入 `import_error`（定位到行号/字段/错误码），合法行正常入库，批次状态与行数/错误数登记 `import_batch`。

#### Scenario: 混合质量 CSV 导入

- **WHEN** 导入 100 行 CSV 其中 5 行非法（如数量为负、SKU 不存在）
- **THEN** 95 行入库成功，5 行进 `import_error` 且 `raw_value`/`row_no`/`field_name`/`error_code` 齐全；向导错误页可按行定位并展示修复建议

#### Scenario: 重复文件导入提示

- **WHEN** 再次导入相同 `file_hash` 的文件
- **THEN** 系统提示已导入过（不静默重复入库）

### Requirement: EngineDataset 适配器

系统 SHALL 从本地库（sku、inventory_event、stock_snapshot）构造 `contracts.EngineDataset` 并调用 `validate_dataset`，UI 无引擎分支逻辑（组合根注入不变）。

#### Scenario: 本地数据喂给 FakeEngine

- **WHEN** 本地库存在导入后的主数据与事件，用户发起分析
- **THEN** 构造的 `EngineDataset` 通过 `validate_dataset`，FakeEngine 返回结果并原样持久化到 `analysis_run`/`analysis_result`

#### Scenario: 校验失败阻断

- **WHEN** 数据集校验失败（如引用不存在的 SKU）
- **THEN** 展示错误列表，不调用 `analyze`（沿 M0 行为）

### Requirement: 报告导出

系统 SHALL 为每次分析运行生成 HTML 与 CSV 报告，产物路径与 SHA-256 登记 `report_artifact`（`(run_id, format) UNIQUE`），报告与 `run_id` 一一对应可追溯。

#### Scenario: 导出并重开

- **WHEN** 对历史 run 导出 CSV 报告
- **THEN** 文件生成、`report_artifact` 落行；再次按 run_id 查看时指标内容与持久化结果一致

### Requirement: 备份与恢复安全

系统 SHALL 提供 VACUUM INTO 全量备份（SHA-256 校验值 + `backup_record` 登记）；恢复流程先恢复到临时库校验（schema 版本 + 关键表行数），校验通过才切换，失败保留当前库不动。

#### Scenario: 备份校验

- **WHEN** 执行手动备份
- **THEN** 生成备份文件与 SHA-256，`backup_record.status=VERIFIED`（读取复验通过）

#### Scenario: 恢复失败保护

- **WHEN** 恢复一个损坏/校验不过的备份
- **THEN** 当前数据库原样保留（无任何改动），`backup_record` 记录失败原因

## MODIFIED Requirements

### Requirement: 分析用例数据源（原 M0 fixture 驱动）

分析用例 SHALL 支持两种数据来源：本地库适配器（M1 默认）与 golden fixture（测试保留）；选择逻辑在组合根，presentation 层不感知差异。

### Requirement: 数据字典与 ER 图（M0 版）

`docs/data-dictionary.md` 与 `docs/er-diagram.md` SHALL 扩展收录 M1 全部新表（只加不改，字段与迁移 DDL 100% 对齐）；`scripts/verify_schema.py` 同步扩展校验新表存在性与关键约束。
