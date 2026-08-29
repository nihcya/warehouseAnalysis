# Skill：kpi（库存核心 KPI 计算）

## 元信息

| 项 | 值 |
|---|---|
| 名称 | `kpi` |
| 版本 | 0.2.0 |
| 状态 | implemented（M1 已实现，公式 0.1.0 已冻结） |
| 绑定 engine 版本 | `>=0.2.0,<0.3.0` |
| 公式版本 | 0.1.0（已冻结，A/B 签字，见 `docs/formula-spec.md` §3） |

## 用途

计算指定时间区间与仓库范围内各品类/SKU 的核心库存 KPI：出货率、出货速度、周转率、周转天数、COGS 与库存价值等。本 Skill 只编排参数校验、计算顺序与结果组合，全部计算委托 `warehouse-engine` 完成——**F-KPI-001~008 与 F-COGS-001 已在 engine 0.2.0 真实实现并进入默认 `AnalysisResult`**（实现位于 `packages/warehouse-engine/src/warehouse_engine/calculators/inventory_kpi.py`，事件重放内核 `replay.py`）。

## 输入

- 请求：`AnalysisRequest`，校验依据 `../packages/contracts-schema/analysis-request.schema.json`（contracts 1.0）。
- `EngineDataset` 所需数据集：`sku`（SKU 主数据：品类、单位等）、`movement`（出入库流水：`sku_id`、`occurred_at`、`move_type`、`quantity`、`unit_cost`、`warehouse_id`，可选 `reversal_of` 冲销引用——contracts 0.2.0 新增可选字段）、`snapshot`（库存快照：`quantity`、`inventory_value`，对应本地库 `stock_snapshot`，用于库存价值的快照回退）。

## 输出

符合 `../packages/contracts-schema/analysis-result.schema.json` 的 `AnalysisResult`（含 `run_id`、`engine_version`、`formula_version`、`input_summary` 与 `data_quality` 数据质量报告），产出 `ResultMetric`（公式 ID 与 `docs/formula-spec.md` §3 一致，`sample_count` 为参与统计的 SKU 数）：

- 期初库存 `F-KPI-001（KPI.OPENING_QTY）`、期末库存 `F-KPI-002（KPI.CLOSING_QTY）`、出库量 `F-KPI-003（KPI.OUT_QTY）`
- 动销率 `F-KPI-004（KPI.ACTIVE_RATIO）`、库存价值 `F-KPI-005（KPI.INVENTORY_VALUE）`
- 销售成本 `F-COGS-001（KPI.COGS，移动加权平均）`
- 周转率 `F-KPI-006（KPI.TURNOVER）`、周转天数 `F-KPI-007（KPI.TURNOVER_DAYS）`、覆盖天数 `F-KPI-008（KPI.COVERAGE_DAYS）`

数值以字符串传输（Decimal 语义）：金额 HALF_UP 至 0.01，数量保留输入 scale，比率/天数保留 6 位小数。指标不可计算时 `value` 输出字符串 `"null"` 并在 `reason` 字段（contracts 0.2.0 新增可选字段）标注原因，不伪造 0。

## 公式步骤（engine 0.2.0 已实现）

口径冻结于 `docs/formula-spec.md` §3（formula_version 0.1.0），本文件不复制公式全文：

1. **事件重放**（`warehouse_engine/replay.py`）：全部参与事件按 `(move_date, occurred_at, event_id)` 升序重放，期间归属左闭右开 `[start_date, end_date)`；含冲销引用链解析（撤销的撤销 = 复原）、盘点余额替换、调拨 no-op、负余额跟踪与移动加权平均成本状态。F-KPI-001/002/003 与 F-COGS-001 共享该内核。
2. **指标聚合**（`warehouse_engine/calculators/inventory_kpi.py`）：SKU 级指标 = 各 `(sku_id, warehouse_id)` 仓库分桶之和，聚合值 = 各 SKU 之和；F-KPI-004 的分母为 `closing_qty > 0` 或期间有事件的 SKU 数；单位成本口径（§3.5）取该 SKU 最近一次携带 `unit_cost` 的入库类事件。
3. **舍入与输出**：中间计算不舍入；输出时金额 HALF_UP 至 0.01、数量保留输入 scale、比率/天数保留 6 位小数（§2.2）。

实现一致性由黄金数据测试保障（`tests/engine/test_golden.py`，双版本参数化）：

- `tests/fixtures/golden/v0.1.0/`：M0 冻结校验层 + M1 扩展指标数值层（正常周转主路径）。
- `tests/fixtures/golden/v0.2.0/`：M1 新增（历史盘点/冲销构成的期初、期末 0 与负余额、退货冲减出库量为负、UNIT_COST_MISSING 价值回退、期内冲销等边界）。
- 容差按 formula-spec §10：金额 abs≤0.01 或 rel≤1e-6、数量 abs≤0.001 或 rel≤1e-9、比率/天数 abs≤1e-6 或 rel≤1e-6（满足其一即通过）；Warning 按 code+fields 完全一致。

## 参数与默认值

| 参数 | 默认值 | 说明 |
|---|---|---|
| `aggregation_level` | `category` | 聚合粒度：`sku` / `category` / `warehouse` |
| `turnover_annualized` | `false` | 周转率是否年化（formula-spec F-KPI-006 冻结为期间值，不年化） |
| `include_returns` | `true` | 出库量是否扣除退货 |
| `negative_balance_policy` | `warn` | 负库存处理：`warn` / `reject` |

上表默认值即 formula-spec 0.1.0 冻结口径，engine 0.2.0 按冻结默认口径计算（周转率为期间值、退货冲减出库量、负库存 `warn` 且余额照常参与计算）；聚合粒度/年化等参数化切换属后续版本开放。必填参数缺失且无默认值时返回 `PARAM_MISSING`（KPI 类参数均有冻结默认值，M1 路径不触发）。

## 错误码与 Warning 码

- 阻断错误（`analyze` 抛 `DataValidationError`，details 定位到分区/行/字段）：`DATA_VALIDATION_FAILED`（请求不符合 Schema、类型/枚举/精度非法，冲销引用缺失或成环、盘点实盘数量非正——后两者为 M1 新增校验）、`SKU_NOT_FOUND`（`sku_id` 不在 SKU 主数据）、`DUPLICATE_EVENT`（重复 `event_id`）。
- Warning（均非阻断，随 `warnings` 返回并按码汇总进 `data_quality`）：
  - `NEGATIVE_BALANCE`：事件回放后任一 `(sku_id, warehouse_id)` 桶余额为负，fields 依次含 sku_id、warehouse_id、首个负余额日期与数值；期末库存原样输出，期内任一时点余额 < 0 时该 SKU 期间 COGS 记 0（§3.6 边界 1），其他 SKU 不受影响。
  - `UNIT_COST_MISSING`：金额类口径缺 `unit_cost`（fields: sku_id）——出库成本按 0 计（§3.6 边界 2）；库存价值回退该 SKU 最近一条 `SnapshotRecord.inventory_value`，快照亦缺失时该 SKU 价值记 null、不计入合计（§3.5），SKU 不静默丢弃。
  - `PERIOD_MISMATCH`：`move_date` 超出 `[start_date, end_date)`（历史事件照常参与期初重放，`end_date` 当日事件不计入）。
  - `ANALYSIS_PLACEHOLDER`：随结果标注 abc-aging/replenishment/forecasting/benchmark 四类仍为占位。

## 数据不足行为与降级策略（样本不足 → null + reason）

- 空数据集：`sample_count=0`，动销率输出 `"null"`（reason=`empty_dataset`），不发 Warning；不视为失败。
- 平均库存价值 ≤ 0 或 COGS ≤ 0：周转率、周转天数输出 `"null"`（reason=`avg_inventory_value_nonpositive` / `turnover_nonpositive`）。
- 期末库存 ≤ 0 / 期间天数 0 / 期间无出库：覆盖天数输出 `"null"`（reason=`nonpositive_closing` / `zero_period_days` / `no_outflow`）。
- `unit_cost` 缺失：金额类指标降级（库存价值回退快照口径或该 SKU 记 null、不计入合计），附 `UNIT_COST_MISSING`。
- 快照缺失：期初/期末库存由事件重放推导（重放内核天然支持，不依赖快照）。
- 契约 `ResultMetric.value` 不支持 null 类型，null 指标统一以字符串 `"null"` + `reason` 原因标注，不编造数值。

## 权限与资源限制

- 无文件读写、无 Shell 执行、无网络访问、不下载代码；仅消费调用方传入的内存数据集。
- 性能阈值（M1 基线，详见 `docs/m1-handover-b.md`）：1 万行 analyze ≤5 秒、10 万行 analyze ≤40 秒（全链路 ≤60 秒）；100 万行阈值 M3 冻结（当前实测 analyze 171.25 秒）。

## 变更记录

- 0.2.0（2026-08-29）：M1 实现对齐——F-KPI-001~008、F-COGS-001 由 engine 0.2.0 真实实现并进入默认结果；公式步骤从占位改为实现引用（replay 重放内核 + inventory_kpi 计算器）；补充黄金数据位置（v0.1.0 指标层扩展 + v0.2.0 新增）、错误码/Warning 实际行为与样本不足降级行为（null + reason）；engine 绑定范围升至 `>=0.2.0,<0.3.0`。
- 0.1.0（2026-08-29）：初始骨架（M0，skeleton）。

## 兼容矩阵

| 组件 | 版本 | 兼容要求 |
|---|---|---|
| warehouse-engine | 0.2.0 | 本 Skill 绑定 `>=0.2.0,<0.3.0` |
| contracts | 1.0（contracts-python 0.2.0） | 输入/输出 Schema 与 Pydantic 模型一致；0.2.0 新增可选字段（`reversal_of`、`ResultMetric.reason`、`AnalysisResult.data_quality`）为非破坏变更 |
| formula | 0.1.0 | 口径见 `docs/formula-spec.md` §3，已冻结（A/B 签字，2026-08-29） |
