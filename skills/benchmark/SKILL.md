# Skill：benchmark（行业基准比较）

## 元信息

| 项 | 值 |
|---|---|
| 名称 | `benchmark` |
| 版本 | 0.3.0 |
| 状态 | implemented（M2 已实现，公式 0.1.0 已冻结） |
| 绑定 engine 版本 | `>=0.3.0,<0.4.0` |
| 公式版本 | 0.1.0（已冻结，A/B 签字，见 `docs/formula-spec.md` §9） |

## 用途

将本仓库分析结果与行业基准对比，输出偏离度与异常标记，为经营诊断提供外部参照。本 Skill 只编排基准注入、匹配与结果组合，全部计算委托 `warehouse-engine` 完成——**F-BM-001 已在 engine 0.3.0 真实实现并进入默认 `AnalysisResult`**（实现位于 `packages/warehouse-engine/src/warehouse_engine/calculators/benchmark_compare.py`）。

## 输入

- 请求：`AnalysisRequest`（`../packages/contracts-schema/analysis-request.schema.json`）。`EngineDataset` 无基准表（contracts 1.0 未含），**基准记录由调用方经 `request.parameters["benchmarks"]` 注入**——本计算器不读文件、不访问网络，符合 B 侧边界约束；版本化基准数据集随 fixture 维护（`tests/fixtures/benchmarks/v0.1.0.json`）。
- 注入的基准记录字段：`source` / `region` / `industry` / `sample_scope` / `updated_at` / `benchmark_version` / `unit` / `applicability`（八字段）加 `metric` 与 `value`。

## 输出

`AnalysisResult` 内的 `ResultMetric`（公式 ID 与 `docs/formula-spec.md` §9 一致，数据集级聚合）：

- 相对偏离度 `F-BM-001（BM.DEVIATION_RATIO）`：各比较项相对偏差的算术平均（%）。各指标单位不同（CNY / 件 / 比率 / 天数），跨单位求和无意义，故取均值（M2 实现决策，已注明）。

数值以字符串传输（Decimal 语义）：比率保留 6 位小数。无匹配时 `value` 输出 `"null"` 并在 `reason=benchmark_unavailable` 标注。

## 公式步骤（engine 0.3.0 已实现）

口径冻结于 `docs/formula-spec.md` §9，本文件不复制公式全文：

1. **字段校验**：八字段加 `metric`/`value` 任一缺失、为空或 `updated_at`/`value` 无法解析即丢弃该记录（§9 原文：不得使用），绝不引入无来源说明的经验数字兜底。
2. **匹配**：按 `(industry, region, metric)` 命中，请求侧 `region` 缺省取「全国」口径；多条命中按 `(benchmark_version, updated_at)` 取最新，并列时按 `source` 字典序（版本号按数字分量解析比较，确定性）。
3. **无匹配**：非阻断 `BENCHMARK_UNAVAILABLE`（fields: `[industry, region]`），不输出任何比较结论，指标 `null` + `reason=benchmark_unavailable`。
4. **聚合**：`BM.DEVIATION_RATIO` 取各比较项相对偏差的算术平均（见上）。

实现一致性由黄金数据测试保障（`tests/engine/test_golden.py` + `tests/engine/test_benchmark_compare.py` 覆盖匹配/元数据完整性/无匹配/零基准值边界）：

- `v0.4.0`：注入 `KPI.TURNOVER=1.00` 合规记录，商户值 0.826087 → 相对偏差 −0.173913。
- 容差与 Warning 一致性同 §10。

## 参数与默认值

| 参数 | 默认值 | 说明 |
|---|---|---|
| `benchmarks` | 无 | 基准记录列表（经 `request.parameters` 注入）；缺失即无匹配 |
| `industry` | 空 | 行业标签（匹配键） |
| `region` | `全国` | 区域标签（匹配键，缺省取全国口径） |

## 错误码与 Warning 码

- 阻断错误：`DATA_VALIDATION_FAILED` / `SKU_NOT_FOUND` / `DUPLICATE_EVENT`（与 M0/M1 一致）。
- Warning（非阻断）：
  - `BENCHMARK_UNAVAILABLE`：无匹配基准（fields: `[industry, region]`），指标 `null` + `reason=benchmark_unavailable`。
  - `NEGATIVE_BALANCE` / `PERIOD_MISMATCH` / `UNIT_COST_MISSING` / `PARAM_MISSING`：跨计算器共享。

## 数据不足行为与降级策略（样本不足 → null + reason）

- 未注入基准 / 行业不匹配：F-BM-001 `null` + `reason=benchmark_unavailable`，发 `BENCHMARK_UNAVAILABLE`，不视为失败。
- 基准值为 0：相对差异无定义 → `relative_diff` 为 None，该比较项不进入均值；无其他比较项时标注 `zero_benchmark_value`。
- 指标值为 `"null"`：不参与比较。
- null 指标统一以字符串 `"null"` 表示。

## 权限与资源限制

- 无文件读写、无 Shell 执行、无网络访问、不下载代码；仅消费调用方经 `request.parameters` 注入的内存基准记录。
- 性能阈值复用 M1 基线（见 `docs/m1-handover-b.md` §5）。

## 变更记录

- 0.1.0（2026-08-29）：初始骨架（M0，skeleton）。
- 0.3.0（2026-08-30）：M2 实现 F-BM-001，状态转 implemented，绑定 engine `>=0.3.0,<0.4.0`。

## 兼容矩阵

| 组件 | 版本 | 兼容要求 |
|---|---|---|
| warehouse-engine | 0.3.0 | 本 Skill 绑定 `>=0.3.0,<0.4.0` |
| contracts | 1.0 | 输入/输出 Schema 与 Pydantic 模型一致（`schema_version` 不变） |
| formula | 0.1.0 | 口径见 `docs/formula-spec.md` §9，已冻结 |
