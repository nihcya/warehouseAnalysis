# 开发者 B：M1 标准化与 KPI 引擎 Spec

## Why

M0 引擎基线已交付并完成联调闭环：A 的平台基线（control-plane、工作台、Web）已通过 PR #2 合入 `master`，工作台已接入 `FakeEngine`；`docs/formula-spec.md` 0.1.0 已由 A、B 双方签字冻结（本地未提交）。按 `开发需求-B引擎Skill.md` §7 M1，引擎需从"校验 + 占位结果"升级为"完整校验 + KPI/COGS 真实计算 + 确定性输出"，并首次交付 A 可消费的版本化 wheel。

## What Changes

- **分支**：从 `origin/master`（aeaab9d）创建 `feature/b-m1-kpi-engine`；首个提交归档 formula-spec 冻结签字（关闭 M0 遗留 Task 2.5）。工作区中用户的其他未提交改动（`README.md`、`docs/log.txt`）原样保留，不回滚、不纳入本 spec 提交范围。
- **校验器补全**：实现 formula-spec §2 剩余校验语义——事件归属期间（`[start_date, end_date)` 左闭右开、`move_date` 折算）、冲销引用不存在事件（阻断）、盘点 `on_hand_qty` 合法性、日期逻辑异常（乱序可容忍按重放规则排序；引用与结构错误阻断）。
- **KPI/COGS 计算器实现**：`F-KPI-001~008`、`F-COGS-001` 按 formula-spec §3 冻结口径实现（期初/期末库存、出库量、动销率、库存价值含 `UNIT_COST_MISSING` 回退、COGS 移动加权平均、周转率/周转天数、覆盖天数）。lot_id 不参与 M1 计算（主基线约定）。
- **analyze() 实装 KPI 路径**：`analyze` 返回真实 KPI 结果 + 数据质量报告（ValidationReport 汇总），KPI 部分不再返回 `ANALYSIS_PLACEHOLDER`；abc-aging / replenishment / forecasting / benchmark 四个计算器维持占位（M2/M3 范围）。
- **黄金数据指标层扩展**：`tests/fixtures/golden/` 的 expected 从校验层扩展到指标数值层（含 formula-spec §10 容差），新增含盘点、退货、冲销、负库存、UNIT_COST_MISSING 回退用例。
- **交付物**：engine `0.2.0` 版本化 wheel + `SHA256SUMS`，性能基线数据（perf_bench 1 万/10 万行）与 M1 阈值写入交接文档。
- **文档与 Skill**：`skills/kpi/SKILL.md` 公式步骤从占位转实现引用、`skills/manifest.json` engine 版本范围更新、`CHANGELOG.md`、新增 `docs/m1-handover-b.md`（M1 交接说明）。

contracts 预期不变（`AnalysisResult` 已含 KPI 结果结构）；若实现中发现必须新增字段，走 `schema_version` 1.0 → 1.1 minor 变更（非破坏，向后兼容），并同步重导出 JSON Schema（CI 漂移门禁看护）。

## Impact

- Affected specs: `deliver-m0-engine-baseline`（其 Task 2.5 遗留项由本 spec 首个提交关闭；Task 1.5/7.1 已随 A 的 PR #2 承接关闭）。
- Affected code: `packages/warehouse-engine/src/warehouse_engine/`（`engine.py`、`validation/`、`calculators/inventory_kpi.py`、`errors.py`）、`tests/`（golden fixtures 扩展、契约/属性/单元测试）、`skills/`（kpi SKILL.md、manifest.json）、`docs/`（formula-spec.md 冻结签字提交、m1-handover-b.md、CHANGELOG.md）。不修改 A 侧代码（services/、apps/、local-data/）。

## ADDED Requirements

### Requirement: 事件重放与校验补全

系统 SHALL 按 formula-spec §2 实现完整校验与重放语义：期间左闭右开归属、同 SKU 内按 `(move_date, occurred_at, event_id)` 升序重放、冲销引用校验、盘点余额替换、负库存 Warning（非阻断）。

#### Scenario: 冲销引用不存在的事件
- **WHEN** 冲销事件引用的 `event_id` 在数据集中不存在
- **THEN** 校验返回阻断错误 `DATA_VALIDATION_FAILED`，details 定位到该冲销事件

#### Scenario: 期间归属
- **WHEN** 事件 `move_date` 落在 `[start_date, end_date)` 之外
- **THEN** 该事件不计入本期指标，但期初计算仍重放 `move_date < start_date` 的全部历史事件

### Requirement: KPI/COGS 计算器

系统 SHALL 按 formula-spec §3 实现 F-KPI-001~008 与 F-COGS-001，输出携带 `formula_id`、`formula_version`（"0.1.0"）、数据期间、`sample_count` 四个公共字段；中间计算不舍入，仅在指标输出时舍入（金额 HALF_UP 至 0.01，数量保留输入 scale，比例类保留 6 位小数）。

#### Scenario: COGS 移动加权平均
- **WHEN** 期间内存在多次入库且 `unit_cost` 各不相同
- **THEN** COGS 按移动加权平均成本计算，与黄金数据手工核算值在容差内一致

#### Scenario: 库存价值 UNIT_COST_MISSING 回退
- **WHEN** 某 SKU 期末余额 > 0 但事件缺失 `unit_cost`
- **THEN** 库存价值按冻结口径回退处理，并发 Warning `UNIT_COST_MISSING`（fields 含 sku_id），不静默丢弃该 SKU

#### Scenario: 负库存
- **WHEN** 重放中任一 `(sku_id, warehouse_id)` 余额 < 0
- **THEN** 余额照常参与计算，结果原样输出，并发非阻断 Warning `NEGATIVE_BALANCE`（fields 含 sku_id、warehouse_id、首个负余额日期与数值）

### Requirement: analyze 确定性与数据质量报告

`analyze` SHALL 对同一输入产出逐字节可复现的结果（无随机性、无时间依赖），并随结果返回数据质量报告；KPI 部分不再出现 `ANALYSIS_PLACEHOLDER`。

#### Scenario: 确定性
- **WHEN** 同一 `(request, dataset)` 连续执行两次 analyze
- **THEN** 两次 AnalysisResult 序列化结果完全一致

#### Scenario: 数据质量报告
- **WHEN** 数据集含负库存与缺失成本事件
- **THEN** 数据质量报告按 Warning 码汇总（计数 + 明细），随 AnalysisResult 一并返回

### Requirement: 黄金数据指标层验收

黄金数据 SHALL 覆盖每个 KPI 公式 ID 的至少一个数值断言（含容差），并覆盖 formula-spec §3 各公式声明的边界用例（空历史、含盘点、退货与冲销、期末为 0/负、UNIT_COST_MISSING）。

#### Scenario: 黄金回归
- **WHEN** CI 运行黄金数据测试
- **THEN** 全部指标数值在声明容差内通过；任何口径改动导致数值变化时必须先更新 formula-spec 并升版本

### Requirement: wheel 交付与性能基线

系统 SHALL 构建并归档 engine `0.2.0` wheel 与 `SHA256SUMS`，性能脚本输出 1 万/10 万行基线数据并设定 M1 阈值，记录于 M1 交接文档。

#### Scenario: wheel 可安装
- **WHEN** A 在独立环境中 `pip install` 该 wheel
- **THEN** `WarehouseEngine` / `FakeEngine` / `contracts` 公开入口全部可用，无需源码目录

## MODIFIED Requirements

### Requirement: analyze 返回占位结果（M0 临时行为）

M0 的 `analyze` 对全部指标返回 `ANALYSIS_PLACEHOLDER` 占位结果。M1 起该行为收窄为：KPI/COGS 返回真实计算结果；其余四类（ABC/库龄/呆滞/补货/预测/基准）在 M2/M3 实现前继续占位并保留占位 Warning。
**Migration**: A 工作台已按 contracts 消费 `AnalysisResult` 结构，无接口变更；仅占位 Warning 范围缩小。

## REMOVED Requirements

无。
