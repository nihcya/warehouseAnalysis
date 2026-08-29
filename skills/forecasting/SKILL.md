# Skill：forecasting（需求预测与误差评估）

## 元信息

| 项 | 值 |
|---|---|
| 名称 | `forecasting` |
| 版本 | 0.1.0 |
| 状态 | skeleton（M0 骨架，公式未冻结） |
| 绑定 engine 版本 | `>=0.1.0,<0.2.0` |
| 公式版本 | 0.1.0（见 `docs/formula-spec.md`） |

## 用途

基于出库流水构建需求预测（M0 基线为移动平均）并评估预测误差（MAPE/MAE/RMSE），输出模型名称、训练窗口、预测窗口、输入样本数与是否降级到基线模型。本 Skill 只编排「时间切分 → 预测 → 误差评估」的计算顺序与结果组合，全部计算委托 `warehouse-engine`（`calculators/forecasting.py`）完成。

## 输入

- 请求：`AnalysisRequest`，校验依据 `../packages/contracts-schema/analysis-request.schema.json`（contracts 1.0）。
- `EngineDataset` 所需数据集：`movement`（出入库流水：`sku_id`、`occurred_at`、`move_type`、`quantity`；仅出库事件参与需求序列构建）。

## 输出

符合 `../packages/contracts-schema/analysis-result.schema.json` 的 `AnalysisResult`（含 `run_id`、`engine_version`、`formula_version` 与输入摘要），产出 `ResultMetric`（公式 ID 与 `docs/formula-spec.md` 一致）：

- 预测 `F-FCST-001（4 周移动平均基线）`
- 预测误差 `F-FCST-002（MAPE/WAPE/MAE/RMSE）`

每个预测结果记录模型名称、训练窗口、预测窗口、输入样本数、误差指标与是否降级到基线模型。

## 公式步骤

口径以 `docs/formula-spec.md`「预测与误差」小节为准，本文件不复制公式全文；未冻结口径不得进入默认结果：

1. 需求序列构建与时间切分（训练窗口/预测窗口，切分规则见 formula-spec）。
2. 基线模型与移动平均模型预测。
3. 误差评估（MAPE/MAE/RMSE）与样本不足降级判定。

## 参数与默认值

| 参数 | 默认值 | 说明 |
|---|---|---|
| `model` | `moving_average` | 预测模型（M0 仅基线与移动平均） |
| `moving_average_window` | `4` | 移动平均窗口（周，formula-spec 冻结 4 周移动平均基线） |
| `training_window_days` | `90` | 训练窗口（天，占位） |
| `forecast_horizon_days` | `28` | 预测窗口（天；formula-spec 冻结 split_date 缺省 = end_date − 28 天） |
| `min_samples` | `12` | 样本不足阈值（期；formula-spec §8.3 冻结 n < 12 强制降级基线） |

默认值与 `docs/formula-spec.md` 口径一致（移动平均 4 期、预测窗口 28 天、样本不足阈值 12 期）；口径冻结或变更以该文档为准；必填参数缺失且无默认值时返回 `PARAM_MISSING`。

## 错误码与 Warning 码

- 阻断错误：`DATA_VALIDATION_FAILED`（请求不符合 Schema、类型/枚举/精度非法）、`PARAM_MISSING`（必填参数缺失且无默认值）。
- Warning：`DATE_MISSING`（事件日期缺失，无法构建时间序列）、`NO_OUTFLOW`（需求序列为零或无出库）、`INSUFFICIENT_SAMPLES`（样本不足，降级到基线模型）、`DUPLICATE_EVENT`（疑似重复事件，影响需求统计）。

## 数据不足行为与降级策略

- 样本数低于 `min_samples`：降级到基线模型（最近均值/持平），误差指标标记为不适用，附 `INSUFFICIENT_SAMPLES`。
- 零需求或无出库：预测为 0 并附 `NO_OUTFLOW`，不参与 MAPE 达标判定。
- 日期缺失：跳过该 SKU 并附 `DATE_MISSING`，不插补数据。
- 异常值与季节性不稳定：按 formula-spec 口径处理并产生可读 Warning，不隐藏为正常结果。
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
