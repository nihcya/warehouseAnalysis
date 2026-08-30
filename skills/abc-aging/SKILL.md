# Skill：abc-aging（ABC 分类、库龄与呆滞识别）

## 元信息

| 项 | 值 |
|---|---|
| 名称 | `abc-aging` |
| 版本 | 0.3.0 |
| 状态 | implemented（M2 已实现，公式 0.1.0 已冻结） |
| 绑定 engine 版本 | `>=0.3.0,<0.4.0` |
| 公式版本 | 0.1.0（已冻结，A/B 签字，见 `docs/formula-spec.md` §4~§6） |

## 用途

对指定时间区间内的 SKU 做 ABC 价值分层、库龄区间分布统计与呆滞库存识别，为补货策略与库位管理提供结构化输入。本 Skill 只编排「ABC 排序 → 库龄分桶 → 呆滞标记」的计算顺序与结果组合，全部计算委托 `warehouse-engine` 完成——**F-ABC-001 / F-AGE-001 / F-STALE-001 已在 engine 0.3.0 真实实现并进入默认 `AnalysisResult`**（实现位于 `packages/warehouse-engine/src/warehouse_engine/calculators/abc_aging.py`，依赖事件重放内核 `replay.py` 的逐日出库、出入库日期索引与批次/全历史加权聚合）。

## 输入

- 请求：`AnalysisRequest`，校验依据 `../packages/contracts-schema/analysis-request.schema.json`（contracts 1.0）。
- `EngineDataset` 所需数据集：`sku`（SKU 主数据：品类、单位等）、`movement`（出入库流水：`sku_id`、`occurred_at`、`move_type`、`quantity`、`unit_cost`、`lot_id`、`warehouse_id`）、`snapshot`（库存快照：仅用于 KPI 库存价值回退，库龄优先走批次/流水口径）。

## 输出

符合 `../packages/contracts-schema/analysis-result.schema.json` 的 `AnalysisResult`（含 `run_id`、`engine_version`、`formula_version`、`input_summary` 与 `data_quality` 数据质量报告），产出 `ResultMetric`（公式 ID 与 `docs/formula-spec.md` §4~§6 一致，数据集级聚合，`sample_count` 为参与统计的 SKU 数）：

- ABC 分类 `F-ABC-001（ABC.SKU_COUNT）`：参与分类的 SKU 数（各 SKU 的 A/B/C 归属与出库金额留在计算器返回对象内供测试断言，不对外暴露，符合 §2.2 结果形态约定）。
- 库龄分布 `F-AGE-001（AGE.DIST_QTY / AGE.DIST_AMOUNT）`：五档（左闭右开 0/30/60/90/180 天）的 SKU 数与金额合计。
- 呆滞判定 `F-STALE-001（STALE.COUNT / STALE.AMOUNT / STALE.RATE）`：呆滞 SKU 数、呆滞金额与呆滞库存率。

数值以字符串传输（Decimal 语义）：金额 HALF_UP 至 0.01，数量保留输入 scale，比率/天数保留 6 位小数。指标不可计算时 `value` 输出字符串 `"null"` 并在 `reason` 字段标注原因，不伪造 0。

## 公式步骤（engine 0.3.0 已实现）

口径冻结于 `docs/formula-spec.md` §4~§6（formula_version 0.1.0），本文件不复制公式全文：

1. **ABC（§4）**：排序指标 `amt = max(0, out_qty) × unit_cost`（§3.5 口径）；两级稳定排序（先 `sku_id` 升序、再 `amt` 降序）保证并列值按 `sku_id` 字典序、同输入必同输出；累计占比 `cum ≤ 0.80 → A`、≤ 0.95 → B、其余 C；`amt = 0` 一律归 C；Σ`amt = 0` 时全归 C 并标注 `zero_total_amount`；`unit_cost` 缺失时 `amt` 记 0 归 C（不伪造数量口径排序），`UNIT_COST_MISSING` 由 inventory_kpi 统一发出。
2. **库龄（§5）**：观察点 `end_date`；区间左闭右开（30 天归 `[30,60)`）。有 `lot_id` 走批次桶（库龄 = 观察点 − 该批次最近一次入库日期）；无批次走 FIFO 残余切片加权平均；构成不足（盘点/负库存/冲销使残余合计 ≠ 期末余额）回退全历史入库聚合并标注 `all_history`；期末 ≤ 0 不参与；期末 > 0 但全历史无入库事件 → 跳过并发 `DATE_MISSING(no_inbound_event)`，不编造库龄。
3. **呆滞（§6）**：`no_outflow_days = 观察点 − 末次出库日期`（无出库记录回退首次入库日期）；判定为 `no_outflow_days > stale_window_days` 且 `closing_qty > min_stock_qty` 且不满足排除条件（新品豁免 `new_inbound_grace_days` / 停产标记 `discontinued_flag`）。未判呆滞的 SKU 在 `excluded_reason` 标注具体原因，不发 Warning。

实现一致性由黄金数据测试保障（`tests/engine/test_golden.py`，四版本参数化 + `tests/fixtures/golden/v0.3.0/` 专注本公式的边界）：

- `v0.3.0`：6 SKU 覆盖累计占比 80/95 边界、五档库龄（0/30/60/90/180）、新品豁免优先于天数判定、呆滞率 0.25。
- 容差按 formula-spec §10：金额 abs≤0.01 或 rel≤1e-6、数量 abs≤0.001 或 rel≤1e-9、比率/天数 abs≤1e-6 或 rel≤1e-6；Warning 按 code+fields 完全一致。

## 参数与默认值

| 参数 | 默认值 | 说明 |
|---|---|---|
| `abc_threshold_a` | `0.80` | A 类累计占比上限（cum ≤ 0.80 归 A） |
| `abc_threshold_b` | `0.95` | B 类累计占比上限（0.80 < cum ≤ 0.95 归 B） |
| `stale_window_days` | `90` | 呆滞观察窗口（无出库天数阈值，天） |
| `min_stock_qty` | `1` | 呆滞最低库存阈值（closing_qty 严格大于才参与判定） |
| `new_inbound_grace_days` | `30` | 新品豁免窗口（首次入库晚于 `end_date − grace` 视为新品，优先于天数判定） |
| `discontinued_flag` | `false` | 停产 SKU 排除标记（经 `request.parameters` 传 `{sku_id: bool}`） |
| `aging_bucket_edges` | `0,30,60,90,180` | 库龄分桶边界（天，左闭右开） |

默认值与 `docs/formula-spec.md` 口径一致（ABC 阈值 0.80/0.95、呆滞窗口 90 天）；必填参数缺失且无默认值时返回 `PARAM_MISSING`（`stale_window_days`/`min_stock_qty` 有冻结默认值，本公式路径不触发）。

## 错误码与 Warning 码

- 阻断错误（`analyze` 抛 `DataValidationError`，details 定位到分区/行/字段）：`DATA_VALIDATION_FAILED`、`SKU_NOT_FOUND`、`DUPLICATE_EVENT`（与 M0/M1 一致）。
- Warning（均非阻断，随 `warnings` 返回并按码汇总进 `data_quality`）：
  - `NO_OUTFLOW`：观察窗口内无出库（fields: sku_id），参与呆滞候选；无出库金额时 ABC 归 C。
  - `DATE_MISSING`：库龄计算所需入库日期缺失（fields: `[sku_id, "no_inbound_event"]`），该 SKU 跳过库龄分桶，不编造库龄。
  - `UNIT_COST_MISSING`：金额口径缺 `unit_cost`（fields: sku_id），由 inventory_kpi 统一发出，ABC 金额排序降级。
  - `NEGATIVE_BALANCE` / `PERIOD_MISMATCH`：重放层与校验层告警，跨公式计算器共享。

## 数据不足行为与降级策略（样本不足 → null + reason）

- 空数据集：`sample_count=0`，三公式均输出 `"null"`（reason=`empty_dataset`），不发 Warning；不视为失败。
- 期末 > 0 但全历史无入库事件：库龄分布该 SKU 跳过并发 `DATE_MISSING`，不编造库龄。
- 构成不足（盘点/负库存/冲销）：库龄回退 `all_history` 加权口径并标注，不静默丢弃。
- 新品/停产：呆滞判定按 `excluded_reason` 标注（`new_inbound` / `discontinued` / `within_window`），不发 Warning。
- 契约 `ResultMetric.value` 不支持 null 类型，null 指标统一以字符串 `"null"` 表示（contracts 0.2.0 起支持 `reason` 字段）。

## 权限与资源限制

- 无文件读写、无 Shell 执行、无网络访问、不下载代码；仅消费调用方传入的内存数据集。
- 内存上限与单次运行耗时上限：见 `docs/m1-handover-b.md` §5 性能基线（M2 未引入额外阈值，复用 M1 1 万/10 万行阈值）。

## 变更记录

- 0.1.0（2026-08-29）：初始骨架（M0，skeleton）。
- 0.3.0（2026-08-30）：M2 实现 F-ABC-001 / F-AGE-001 / F-STALE-001，状态转 implemented，绑定 engine `>=0.3.0,<0.4.0`。

## 兼容矩阵

| 组件 | 版本 | 兼容要求 |
|---|---|---|
| warehouse-engine | 0.3.0 | 本 Skill 绑定 `>=0.3.0,<0.4.0` |
| contracts | 1.0 | 输入/输出 Schema 与 Pydantic 模型一致（`schema_version` 不变） |
| formula | 0.1.0 | 口径见 `docs/formula-spec.md` §4~§6，已冻结 |
