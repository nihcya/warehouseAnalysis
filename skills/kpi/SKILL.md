# Skill：kpi（库存核心 KPI 计算）

## 元信息

| 项 | 值 |
|---|---|
| 名称 | `kpi` |
| 版本 | 0.1.0 |
| 状态 | skeleton（M0 骨架，公式未冻结） |
| 绑定 engine 版本 | `>=0.1.0,<0.2.0` |
| 公式版本 | 0.1.0（见 `docs/formula-spec.md`） |

## 用途

计算指定时间区间与仓库范围内各品类/SKU 的核心库存 KPI：出货率、出货速度、周转率、周转天数、COGS 与库存价值等。本 Skill 只编排参数校验、计算顺序与结果组合，全部计算委托 `warehouse-engine`（`calculators/inventory_kpi.py`）完成。

## 输入

- 请求：`AnalysisRequest`，校验依据 `../packages/contracts-schema/analysis-request.schema.json`（contracts 1.0）。
- `EngineDataset` 所需数据集：`sku`（SKU 主数据：品类、单位等）、`movement`（出入库流水：`sku_id`、`occurred_at`、`move_type`、`quantity`、`unit_cost`、`warehouse_id`）、`snapshot`（库存快照：`on_hand_qty`，用于期初/期末库存）。

## 输出

符合 `../packages/contracts-schema/analysis-result.schema.json` 的 `AnalysisResult`（含 `run_id`、`engine_version`、`formula_version` 与输入摘要），产出 `ResultMetric`（公式 ID 与 `docs/formula-spec.md` 一致）：

- 期初库存 `F-KPI-001（opening_qty）`、期末库存 `F-KPI-002（closing_qty）`、出库量 `F-KPI-003（out_qty）`
- 动销率 `F-KPI-004（active_ratio）`、库存价值 `F-KPI-005（inventory_value）`
- 销售成本 `F-COGS-001（COGS，移动加权平均）`
- 周转率 `F-KPI-006（turnover）`、周转天数 `F-KPI-007（turnover_days）`、覆盖天数 `F-KPI-008（coverage_days）`

## 公式步骤

口径以 `docs/formula-spec.md`「KPI / COGS」小节为准，本文件不复制公式全文；未冻结口径不得进入默认结果：

1. 期初/期末库存推导（含盘点、退货与负库存处理口径，见 formula-spec 对应小节）。
2. COGS 与库存价值计算（数量/金额均为 Decimal 语义）。
3. 出货率、出货速度、周转率、周转天数与动销、缺货、覆盖天数计算。

## 参数与默认值

| 参数 | 默认值 | 说明 |
|---|---|---|
| `aggregation_level` | `category` | 聚合粒度：`sku` / `category` / `warehouse` |
| `turnover_annualized` | `false` | 周转率是否年化（formula-spec F-KPI-006 冻结为期间值，不年化） |
| `include_returns` | `true` | 出库量是否扣除退货 |
| `negative_balance_policy` | `warn` | 负库存处理：`warn` / `reject` |

默认值与 `docs/formula-spec.md` 口径对齐（周转率为期间值、退货冲减出库量）；口径冻结或变更以该文档为准；必填参数缺失且无默认值时返回 `PARAM_MISSING`。

## 错误码与 Warning 码

- 阻断错误：`DATA_VALIDATION_FAILED`（请求不符合 Schema、类型/枚举/精度非法）、`SKU_NOT_FOUND`（`sku_id` 不在 SKU 主数据）、`PARAM_MISSING`（必填参数缺失且无默认值）。
- Warning：`DUPLICATE_EVENT`（疑似重复事件）、`NEGATIVE_BALANCE`（事件回放后库存为负）、`UNIT_COST_MISSING`（金额类指标缺 `unit_cost`）、`DATE_MISSING`（期间计算所需日期缺失）。

## 数据不足行为与降级策略

- 空数据集：返回空指标列表并在 `summary` 说明，不视为失败。
- 期初库存缺失或为 0：出货率、周转率标记为不可计算并附 Warning，不编造数值。
- `unit_cost` 缺失：金额类指标降级为数量口径，附 `UNIT_COST_MISSING`。
- 快照缺失：以事件回放推导期初/期末库存，附 Warning 说明口径差异。
- 以上为 M0 占位行为，随引擎实现与黄金数据冻结后生效。

## 权限与资源限制

- 无文件读写、无 Shell 执行、无网络访问、不下载代码；仅消费调用方传入的内存数据集。
- 内存上限与单次运行耗时上限：占位（M0 不设阈值，待 M3 性能基线冻结）。

## 变更记录

- 0.1.0（2026-08-29）：初始骨架（M0，skeleton）。

## 兼容矩阵

| 组件 | 版本 | 兼容要求 |
|---|---|---|
| warehouse-engine | 0.1.0 | 本 Skill 绑定 `>=0.1.0,<0.2.0` |
| contracts | 1.0 | 输入/输出 Schema 与 Pydantic 模型一致 |
| formula | 0.1.0 | 口径见 `docs/formula-spec.md`，未冻结 |
