# Skill：benchmark（行业基准对比）

## 元信息

| 项 | 值 |
|---|---|
| 名称 | `benchmark` |
| 版本 | 0.1.0 |
| 状态 | skeleton（M0 骨架，公式未冻结） |
| 绑定 engine 版本 | `>=0.1.0,<0.2.0` |
| 公式版本 | 0.1.0（见 `docs/formula-spec.md`） |

## 用途

将 `kpi` Skill 产出的结构化指标与版本化行业基准对比，计算偏离度并标记异常，为采购方向提供外部参照。本 Skill 只编排「基准匹配 → 偏离度计算 → 标记」的顺序与结果组合，全部计算委托 `warehouse-engine`（`calculators/benchmark_compare.py`）完成，不允许把未经来源说明的经验数字作为默认基准。

## 输入

- 请求：`AnalysisRequest`，校验依据 `../packages/contracts-schema/analysis-request.schema.json`（contracts 1.0）。
- 输入数据：前序 `kpi` Skill 的 `AnalysisResult`（`ResultMetric` 列表）+ `benchmark` 基准数据集（含来源、地区、行业、样本范围、更新时间、版本、指标单位与适用限制）。

## 输出

符合 `../packages/contracts-schema/analysis-result.schema.json` 的 `AnalysisResult`（含 `run_id`、`engine_version`、`formula_version` 与输入摘要），产出 `ResultMetric`（公式 ID 与 `docs/formula-spec.md` 一致）：

- 基准比较 `F-BM-001（benchmark_compare）`：客户实际值与基准值对照、绝对/相对差异与基准偏离标记

基准结果记录来源、地区、行业、样本范围、更新时间、版本、指标单位与适用限制。

## 公式步骤

口径以 `docs/formula-spec.md`「行业基准」小节为准，本文件不复制公式全文：

1. 按 SKU/品类的行业标签匹配基准（含地区与基准版本选择）。
2. 计算各指标偏离度并按阈值标记。
3. 无匹配基准时返回 `BENCHMARK_UNAVAILABLE`，不使用默认经验值。

## 参数与默认值

| 参数 | 默认值 | 说明 |
|---|---|---|
| `industry` | 无默认（必填） | 行业标签，来自参数或 SKU 主数据 |
| `region` | 空（全部地区） | 基准地区过滤 |
| `benchmark_version` | 最新可用版本 | 指定基准数据版本 |
| `deviation_warn_threshold` | `0.20` | 偏离度告警阈值（占位） |

默认值为 M0 占位，冻结值以 `docs/formula-spec.md` 为准；必填参数缺失且无默认值时返回 `PARAM_MISSING`。

## 错误码与 Warning 码

- 阻断错误：`DATA_VALIDATION_FAILED`（请求不符合 Schema或 KPI 结果结构非法）、`SKU_NOT_FOUND`（指标中的 `sku_id` 不在主数据）、`PARAM_MISSING`（`industry` 等必填参数缺失）、`BENCHMARK_UNAVAILABLE`（无匹配行业/地区基准）。
- Warning：基准样本范围过小或基准版本过旧（具体 Warning 码随 M1 冻结，见 formula-spec）。

## 数据不足行为与降级策略

- 无匹配基准：返回 `BENCHMARK_UNAVAILABLE`（阻断），绝不回退到未标注来源的经验数字。
- 部分指标无基准：该指标跳过对比并在 `summary` 说明，其余指标正常输出。
- KPI 结果缺失或结构非法：阻断并返回 `DATA_VALIDATION_FAILED`，不自行推算 KPI。
- 以上为 M0 占位行为，随引擎实现与黄金数据冻结后生效。

## 权限与资源限制

- 无文件读写、无 Shell 执行、无网络访问、不下载代码；仅消费调用方传入的内存数据集与内置版本化基准数据。
- 内存上限与单次运行耗时上限：占位（M0 不设阈值，待 M3 性能基线冻结）。

## 变更记录

- 0.1.0（2026-08-29）：初始骨架（M0，skeleton）。

## 兼容矩阵

| 组件 | 版本 | 兼容要求 |
|---|---|---|
| warehouse-engine | 0.1.0 | 本 Skill 绑定 `>=0.1.0,<0.2.0` |
| contracts | 1.0 | 输入/输出 Schema 与 Pydantic 模型一致 |
| formula | 0.1.0 | 口径见 `docs/formula-spec.md`，未冻结 |
