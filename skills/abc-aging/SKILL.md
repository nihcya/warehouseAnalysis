# Skill：abc-aging（ABC 分类、库龄与呆滞识别）

## 元信息

| 项 | 值 |
|---|---|
| 名称 | `abc-aging` |
| 版本 | 0.1.0 |
| 状态 | skeleton（M0 骨架，公式未冻结） |
| 绑定 engine 版本 | `>=0.1.0,<0.2.0` |
| 公式版本 | 0.1.0（见 `docs/formula-spec.md`） |

## 用途

对指定时间区间内的 SKU/品类做 ABC 价值分层、库龄区间分布统计与呆滞库存识别，为补货策略与库位管理提供结构化输入。本 Skill 只编排「ABC 排序 → 库龄分桶 → 呆滞标记」的计算顺序与结果组合，全部计算委托 `warehouse-engine`（`calculators/abc_aging.py`）完成。

## 输入

- 请求：`AnalysisRequest`，校验依据 `../packages/contracts-schema/analysis-request.schema.json`（contracts 1.0）。
- `EngineDataset` 所需数据集：`sku`（SKU 主数据）、`movement`（出入库流水：`sku_id`、`occurred_at`、`move_type`、`quantity`、`unit_cost`、`lot_id`）、`snapshot`（库存快照：`on_hand_qty` 与批次信息，用于库龄与呆滞判断）。

## 输出

符合 `../packages/contracts-schema/analysis-result.schema.json` 的 `AnalysisResult`（含 `run_id`、`engine_version`、`formula_version` 与输入摘要），产出 `ResultMetric`（公式 ID 与 `docs/formula-spec.md` 一致）：

- ABC 分类 `F-ABC-001（classification）`：排序指标（出库金额）、累计占比与 A/B/C 归属
- 库龄分布 `F-AGE-001（inventory_age）`：区间分桶（左闭右开）与平均库龄
- 呆滞判定 `F-STALE-001（stale）`：呆滞标记、呆滞金额与呆滞库存率

## 公式步骤

口径以 `docs/formula-spec.md`「ABC / 库龄 / 呆滞」小节为准，本文件不复制公式全文；未冻结口径不得进入默认结果：

1. ABC：按排序指标降序累计占比确定 A/B/C 归属（含累计占比边界与并列值归属规则）。
2. 库龄：按事件或快照口径计算，区间边界统一左闭右开，缺失日期产生 Warning。
3. 呆滞：按观察窗口内无出库天数、最低库存阈值与排除条件标记。

## 参数与默认值

| 参数 | 默认值 | 说明 |
|---|---|---|
| `abc_threshold_a` | `0.80` | A 类累计占比上限（cum ≤ 0.80 归 A） |
| `abc_threshold_b` | `0.95` | B 类累计占比上限（0.80 < cum ≤ 0.95 归 B） |
| `observation_window_days` | `90` | 呆滞观察窗口（天） |
| `deadstock_no_outflow_days` | `90` | 无出库天数阈值（天） |
| `aging_bucket_edges` | `0,30,60,90,180` | 库龄分桶边界（天，左闭右开） |

默认值与 `docs/formula-spec.md` 口径一致（ABC 阈值 0.80/0.95、观察窗口 90 天）；口径冻结或变更以该文档为准；必填参数缺失且无默认值时返回 `PARAM_MISSING`。

## 错误码与 Warning 码

- 阻断错误：`DATA_VALIDATION_FAILED`（请求不符合 Schema、类型/枚举/精度非法）、`SKU_NOT_FOUND`（`sku_id` 不在 SKU 主数据）、`PARAM_MISSING`（必填参数缺失且无默认值）。
- Warning：`DUPLICATE_EVENT`（疑似重复事件）、`NEGATIVE_BALANCE`（事件回放后库存为负）、`DATE_MISSING`（库龄计算所需日期缺失）、`UNIT_COST_MISSING`（ABC 金额排序缺 `unit_cost`）、`NO_OUTFLOW`（观察窗口内无出库，参与呆滞判定）。

## 数据不足行为与降级策略

- 空数据集：返回空指标列表并在 `summary` 说明，不视为失败。
- `unit_cost` 缺失：ABC 排序降级为数量口径，附 `UNIT_COST_MISSING`，结果标注非金额口径。
- 库龄日期缺失：该 SKU/批次跳过库龄分桶并附 `DATE_MISSING`，不编造库龄。
- 观察窗口内无流水：SKU 进入呆滞候选并附 `NO_OUTFLOW`，按排除条件（如窗口内新入库）过滤。
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
