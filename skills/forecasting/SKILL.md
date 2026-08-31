# Skill：forecasting（需求预测与误差评估）

## 元信息

| 项 | 值 |
|---|---|
| 名称 | `forecasting` |
| 版本 | 0.3.0 |
| 状态 | implemented（M2 已实现，公式 0.1.0 已冻结） |
| 绑定 engine 版本 | `>=0.3.0,<0.4.0` |
| 公式版本 | 0.1.0（已冻结，A/B 签字，见 `docs/formula-spec.md` §8） |

## 用途

提供需求预测基线（M0 冻结的 4 周移动平均）与预测误差评估（MAPE/WAPE/MAE/RMSE），为补货提前期内的需求估计提供量化输入；实验模型（Holt-Winters 等）与默认模型**隔离**，不进入默认 `AnalysisResult`。本 Skill 只编排窗口切分、计算顺序与结果组合，全部计算委托 `warehouse-engine` 完成——**F-FCST-001 / F-FCST-002 已在 engine 0.3.0 真实实现并进入默认 `AnalysisResult`**（实现位于 `packages/warehouse-engine/src/warehouse_engine/calculators/forecasting.py`，依赖重放内核 `replay.py` 的逐日出库序列）。

## 输入

- 请求：`AnalysisRequest`（`../packages/contracts-schema/analysis-request.schema.json`）。
- `EngineDataset`：`sku`、`movement`（出库类事件计入需求、`RETURN` 冲减）、`snapshot`。

## 输出

`AnalysisResult` 内的 `ResultMetric`（公式 ID 与 `docs/formula-spec.md` §8 一致，数据集级聚合）：

- 下周需求 `F-FCST-001（FCST.NEXT_WEEK_DEMAND）`：各 SKU 下一周预测之和（件），日均 = 周预测 / 7。
- 平均绝对百分比误差 `F-FCST-002（FCST.MAPE）`：合并（pooled）口径套用 §8.2 公式（%），避免对不可计算 SKU 取均值造成偏倚。

数值以字符串传输（Decimal 语义）：数量保留输入 scale，比率/天数保留 6 位小数，`RMSE` 用 `Decimal.sqrt()`，全链路无 float。指标不可计算时 `value` 输出 `"null"` 并在 `reason` 标注（如 `empty_forecast_window` / `zero_actual_demand` / `insufficient_samples`），不伪造 0。

## 公式步骤（engine 0.3.0 已实现）

口径冻结于 `docs/formula-spec.md` §8，本文件不复制公式全文：

1. **F-FCST-001（§8.1）**：M0 冻结的 4 周移动平均基线。需求按自然周聚合（周一为周首），下一周预测 = 最近 4 周需求算术平均，日均 = 周预测 / 7；可用历史不足 4 周时按实际周数取均值（不补零、不外推），无历史时为 null。
2. **窗口切分（§8.3）**：严格按时间顺序（禁止随机打乱）。训练 `[start_date, split_date)`、预测 `[split_date, end_date)`；`split_date` 支持 date/ISO 串，缺省 `end_date − 28 天`。
3. **F-FCST-002（§8.2）**：滚动一步向前计算——每个预测周取其前 4 个实际周需求均值。`MAPE = (100/n′) × Σ_{d_t>0} |e_t|/d_t`（`d_t=0` 不计入，n′=0 时 null）、`WAPE = Σ|e_t|/Σd_t`（`Σd_t=0` 时 null）、`MAE = Σ|e_t|/n`、`RMSE = √(Σe_t²/n)`，`n` 为预测窗口期数。
4. **样本不足（§8.3）**：有效需求期数（全期间自然周数）< 12 时强制降级基线并发 `INSUFFICIENT_SAMPLES`（fields: `[sku_id, n]`），误差照常输出并标注「不参与达标判定」。

实现一致性由黄金数据测试保障（`tests/engine/test_golden.py` + `tests/fixtures/golden/v0.4.0/` 专注本公式、`tests/engine/test_forecasting.py` 覆盖 split/周聚合/误差/n<12）：

- `v0.4.0`：13 个自然周（缺省 split_date = start_date），SKU-0001 误差全 0、SKU-0002 误差 −20/+30/0，合并 MAPE=25。
- 容差与 Warning 一致性同 §10。

## 参数与默认值

| 参数 | 默认值 | 说明 |
|---|---|---|
| `split_date` | `end_date − 28 天` | 训练/预测切分点（date 或 ISO 串） |
| `moving_average_weeks` | `4` | 移动平均窗口（M0 冻结） |
| `min_demand_periods` | `12` | 有效需求期数阈值，低于即降级 `INSUFFICIENT_SAMPLES` |

## 口径澄清（M2 实现决策，需 A/B 复核，记入 `docs/m2-handover-b.md`）

`§8.2` 的 `n` = **预测窗口期数**（用于 MAE/RMSE 分母）；`§8.3` 的「有效需求期数 n < 12」按**全期间自然周数**理解——若同样按预测窗口理解，则缺省 split_date 下 n≈4 会恒定触发降级，与 §8.4 黄金数据「≥12 期周需求」矛盾。两者在代码中分别命名为 `forecast_periods` 与 `demand_periods`，Warning fields 取后者。

## 错误码与 Warning 码

- 阻断错误：`DATA_VALIDATION_FAILED` / `SKU_NOT_FOUND` / `DUPLICATE_EVENT`（与 M0/M1 一致）。
- Warning（非阻断）：
  - `INSUFFICIENT_SAMPLES`：有效需求期数 < 12（fields: `[sku_id, n]`），降级基线但误差照常输出。
  - `NEGATIVE_BALANCE` / `PERIOD_MISMATCH` / `UNIT_COST_MISSING`：跨计算器共享。

## 数据不足行为与降级策略（样本不足 → null + reason）

- 空数据集：`FCST.NEXT_WEEK_DEMAND` / `FCST.MAPE` 均 `"null"`（reason=`empty_dataset`）。
- 预测窗口为空（`split_date = end_date`）：MAPE `null` + `reason=empty_forecast_window`；逐周需求仍照常输出。
- 预测窗口需求全 0：MAPE/WAPE `null` + `reason=zero_actual_demand`（Σd=0）；MAE/RMSE 不受影响。
- `d_t=0` 期：不计入 MAPE（n′ 相应减少），不静默丢弃。
- null 指标统一以字符串 `"null"` 表示。

## 实验模型隔离（§8.1）

- `experimental/`（类型层 + 导入层 + 依赖层三重隔离）：`ExperimentalForecastResult` 不提供到 `ResultMetric` 的转换，`build_analysis_result(metrics=...)` 在契约层即拒绝；`engine.analyze` 与四个计算器均不 import 本包；默认分析路径不加载 `statsmodels`（由子进程测试断言）。
- `seasonal_naive` 为可运行示例（历史不足 52 周回退最近一期），`holt_winters` 为占位桩（显式报「尚未实装」）；结果携带 `EXPERIMENTAL_DISCLAIMER`，不参与黄金数据验收、不得作为采购依据。

## 权限与资源限制

- 无文件读写、无 Shell 执行、无网络访问、不下载代码；仅消费调用方传入的内存数据集。
- 性能阈值复用 M1 基线（见 `docs/m1-handover-b.md` §5）。

## 变更记录

- 0.1.0（2026-08-29）：初始骨架（M0，skeleton）。
- 0.3.0（2026-08-30）：M2 实现 F-FCST-001~002，状态转 implemented，绑定 engine `>=0.3.0,<0.4.0`；新增 `experimental/` 隔离骨架（不进默认结果）。

## 兼容矩阵

| 组件 | 版本 | 兼容要求 |
|---|---|---|
| warehouse-engine | 0.3.0 | 本 Skill 绑定 `>=0.3.0,<0.4.0` |
| contracts | 1.0 | 输入/输出 Schema 与 Pydantic 模型一致（`schema_version` 不变） |
| formula | 0.1.0 | 口径见 `docs/formula-spec.md` §8，已冻结 |
