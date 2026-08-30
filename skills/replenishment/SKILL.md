# Skill：replenishment（补货建议）

## 元信息

| 项 | 值 |
|---|---|
| 名称 | `replenishment` |
| 版本 | 0.1.0 |
| 状态 | skeleton（M0 骨架，公式未冻结） |
| 绑定 engine 版本 | `>=0.1.0,<0.3.0` |
| 公式版本 | 0.1.0（见 `docs/formula-spec.md`） |

## 用途

基于需求历史与服务水平假设计算安全库存（SS）、补货点（ROP）与建议补货量，输出含在途量、提前期、服务水平假设与限制原因的补货建议。本 Skill 只编排「需求统计 → SS/ROP → 建议量」的计算顺序与结果组合，全部计算委托 `warehouse-engine`（`calculators/replenishment.py`）完成。

## 输入

- 请求：`AnalysisRequest`，校验依据 `../packages/contracts-schema/analysis-request.schema.json`（contracts 1.0）。
- `EngineDataset` 所需数据集：`sku`（SKU 主数据）、`movement`（出入库流水：`sku_id`、`occurred_at`、`move_type`、`quantity`）、`replenishment`（补货参数数据集：`lead_time_days`、在途量、供应商、订购/持有成本等）。

## 输出

符合 `../packages/contracts-schema/analysis-result.schema.json` 的 `AnalysisResult`（含 `run_id`、`engine_version`、`formula_version` 与输入摘要），产出 `ResultMetric`（公式 ID 与 `docs/formula-spec.md` 一致）：

- 安全库存 `F-REPL-001（safety_stock）`
- 补货点 `F-REPL-002（reorder_point）`
- 建议补货量 `F-REPL-003（suggested_qty，扣减当前库存与在途量）`

## 公式步骤

口径以 `docs/formula-spec.md`「补货」小节为准，本文件不复制公式全文；未冻结口径不得进入默认结果：

1. 需求统计：观察窗口内日需求均值与离散度（退货、盘点口径见 formula-spec）。
2. 安全库存与补货点：由服务水平假设、需求离散度与提前期推导（公式见 formula-spec）。
3. 建议补货量：结合当前库存与在途量（EOQ 未在 formula-spec 冻结，不进默认结果）。

## 参数与默认值

| 参数 | 默认值 | 说明 |
|---|---|---|
| `service_level` | `0.95` | 服务水平（对应 Z 值映射见 formula-spec） |
| `observation_window_days` | `90` | 需求统计观察窗口（天） |
| `safety_stock_method` | `normal` | 安全库存计算方法（正态近似） |
| `lead_time_days` | 无默认（必填） | 供应商提前期（天），来自 `replenishment` 数据集 |
| `eoq_ordering_cost` / `eoq_holding_cost` | 无默认 | EOQ 参数；EOQ 未在 formula-spec 冻结，不进默认结果 |

默认值与 `docs/formula-spec.md` 口径一致（服务水平 0.95、Z 值表见该文档 7.4）；口径冻结或变更以该文档为准；必填参数缺失且无默认值时返回 `PARAM_MISSING`。

## 错误码与 Warning 码

- 阻断错误：`DATA_VALIDATION_FAILED`（请求不符合 Schema、类型/枚举/精度非法）、`SKU_NOT_FOUND`（`sku_id` 不在 SKU 主数据）、`PARAM_MISSING`（`lead_time_days` 等必填项缺失）。
- Warning：`DUPLICATE_EVENT`（疑似重复事件）、`NEGATIVE_BALANCE`（事件回放后库存为负）、`INSUFFICIENT_SAMPLES`（需求样本不足，SS/ROP 降级）、`NO_OUTFLOW`（观察窗口内无出库，无法估计需求）。

## 数据不足行为与降级策略

- 需求样本不足：SS/ROP 降级为均值口径（不含离散度项），附 `INSUFFICIENT_SAMPLES`，结果标注置信度受限。
- 观察窗口内无出库：不产出补货建议，附 `NO_OUTFLOW`，`summary` 说明原因。
- EOQ 参数缺失：跳过 EOQ 指标并附 Warning，不影响 SS/ROP。
- 负库存数据：按 `NEGATIVE_BALANCE` Warning 处理并继续，不静默丢弃。
- 以上为 M0 占位行为，随引擎实现与黄金数据冻结后生效。

## 权限与资源限制

- 无文件读写、无 Shell 执行、无网络访问、不下载代码；仅消费调用方传入的内存数据集。
- 内存上限与单次运行耗时上限：占位（M0 不设阈值，待 M3 性能基线冻结）。

## 变更记录

- 0.1.0（2026-08-29）：初始骨架（M0，skeleton）。

## 兼容矩阵

| 组件 | 版本 | 兼容要求 |
|---|---|---|
| warehouse-engine | 0.2.0 | 本 Skill 绑定 `>=0.1.0,<0.3.0`（兼容 0.1.0 FakeEngine 与 0.2.0 真实引擎） |
| contracts | 1.0 | 输入/输出 Schema 与 Pydantic 模型一致 |
| formula | 0.1.0 | 口径见 `docs/formula-spec.md`，未冻结 |
