# Skill：replenishment（补货建议）

## 元信息

| 项 | 值 |
|---|---|
| 名称 | `replenishment` |
| 版本 | 0.3.0 |
| 状态 | implemented（M2 已实现，公式 0.1.0 已冻结） |
| 绑定 engine 版本 | `>=0.3.0,<0.4.0` |
| 公式版本 | 0.1.0（已冻结，A/B 签字，见 `docs/formula-spec.md` §7） |

## 用途

基于期间出库波动与补货提前期，计算安全库存、补货点与建议补货量，为采购计划提供量化输入。本 Skill 只编排参数解析、计算顺序与结果组合，全部计算委托 `warehouse-engine` 完成——**F-REPL-001 / F-REPL-002 / F-REPL-003 已在 engine 0.3.0 真实实现并进入默认 `AnalysisResult`**（实现位于 `packages/warehouse-engine/src/warehouse_engine/calculators/replenishment.py`，依赖重放内核 `replay.py` 的逐日出库序列 `daily_out_by_sku`）。

## 输入

- 请求：`AnalysisRequest`（`../packages/contracts-schema/analysis-request.schema.json`）。
- `EngineDataset`：`sku`、`movement`（出库类事件 `OUTBOUND`/`SCRAP` 计入逐日需求，`RETURN` 冲减）、`snapshot`、`replenishment`（可选 `ReplenishmentRecord`：仅 `lead_time_days` 被本计算器消费；`avg_daily_demand` 与 §7.2 的 `d̄` 口径不同源，本计算器不使用该字段，详见 §3 备注）。

## 输出

`AnalysisResult` 内的 `ResultMetric`（公式 ID 与 `docs/formula-spec.md` §7 一致，数据集级聚合，`sample_count` 为参与统计的 SKU 数）：

- 安全库存 `F-REPL-001（REPL.SS）`：全 SKU 安全库存之和（件）。
- 补货点 `F-REPL-002（REPL.ROP）`：全 SKU 补货点之和（件）。
- 建议补货量 `F-REPL-003（REPL.QTY）`：全 SKU 建议补货量之和（件）。

数值以字符串传输（Decimal 语义）：数量保留输入 scale（§2.2），全链路无 float / `math.sqrt`，`√LT` 用 `Decimal.sqrt()`。指标不可计算时 `value` 输出 `"null"` 并在 `reason` 标注，不伪造 0。

## 公式步骤（engine 0.3.0 已实现）

口径冻结于 `docs/formula-spec.md` §7，本文件不复制公式全文：

1. **F-REPL-001**：`SS = z × σ_d × √LT`。`σ_d` 取重放内核 `daily_out_by_sku` 的逐日需求样本标准差（`n−1` 分母，含零出库日；期间 < 2 天无离散样本时记 0），`√LT` 用 `Decimal.sqrt()`。
2. **F-REPL-002**：`ROP = d̄ × LT + SS`，`d̄ = max(0, out_qty) / period_days`（与 F-KPI-003/F-KPI-008 同一出库量口径）。
3. **F-REPL-003**：`Q* = max(0, ROP − closing_qty − on_order_qty)`，按下限 0 截断。
4. **z 值表**：内置四档 `0.90→1.282 / 0.95→1.645 / 0.98→2.054 / 0.99→2.326`，表外取最近档且差值相等时取较低档（排序键 `(abs(差值), 档位)`，确定性）。
5. **参数优先级**：`dataset.replenishment[sku]` → `request.parameters`（标量或 `{sku_id: value}` 映射）→ 冻结默认值；`service_level` 默认 `0.95`、`on_order_qty` 默认 `0`；`lead_time_days` 无默认值。

实现一致性由黄金数据测试保障（`tests/engine/test_golden.py` + `tests/fixtures/golden/v0.4.0/` 专注本公式、含 `σ_d=0` 边界）：

- `v0.4.0`：SKU-0001 逐日需求恒定（σ_d=0 → SS=0）、SKU-0002 非恒定（σ_d≈15.257924）。
- 容差与 Warning 一致性同 §10。

## 参数与默认值

| 参数 | 默认值 | 说明 |
|---|---|---|
| `lead_time_days` | 无（必填） | 补货提前期（天）；缺失即非阻断 `PARAM_MISSING`，该 SKU 三项降级 |
| `service_level` | `0.95` | 服务水平（z 值查表）；表外取最近档 |
| `on_order_qty` | `0` | 在途量（件），从建议量中扣减 |
| `z_table` | 0.90/0.95/0.98/0.99 | 服务水平→z 值映射（冻结） |

`lead_time_days` 必填且缺省即降级；其余参数均有冻结默认值，正常路径不触发 `PARAM_MISSING`。

## 错误码与 Warning 码

- 阻断错误：`DATA_VALIDATION_FAILED` / `SKU_NOT_FOUND` / `DUPLICATE_EVENT`（与 M0/M1 一致）。
- Warning（非阻断）：
  - `PARAM_MISSING`：必填参数缺失（fields: `[sku_id, 参数名]`，如 `lead_time_days`），该 SKU 三项 `null` + `reason=param_missing`，其余 SKU 继续。
  - `NEGATIVE_BALANCE` / `PERIOD_MISMATCH` / `UNIT_COST_MISSING`：跨计算器共享。

## 数据不足行为与降级策略（样本不足 → null + reason）

- 空数据集 / 期间天数 0：`sample_count=0`，三项输出 `"null"`（reason=`empty_dataset` / `zero_period_days`）。
- `lead_time_days` 缺失：三项 `null` + `reason=param_missing`，发 `PARAM_MISSING`。
- `σ_d = 0`：SS=0、d̄=0 → ROP=SS（照常输出，不降级）。
- 零需求 / 在途量扣减：`Q*` 按下限 0 截断；在途量经 `{sku_id: value}` 映射扣减。
- null 指标统一以字符串 `"null"` 表示。

## 备注（待 A/B 复核，记入 `docs/m2-handover-b.md`）

`ReplenishmentRecord.avg_daily_demand` 与 §7.2 的 `d̄` 口径不同源（后者按公式取 `max(0,out_qty)/period_days`）。本计算器不消费该字段，避免引入未对齐口径；若后续统一，需同步 contracts 与 formula-spec 评审。

## 权限与资源限制

- 无文件读写、无 Shell 执行、无网络访问、不下载代码；仅消费调用方传入的内存数据集。
- 性能阈值复用 M1 基线（见 `docs/m1-handover-b.md` §5）。

## 变更记录

- 0.1.0（2026-08-29）：初始骨架（M0，skeleton）。
- 0.3.0（2026-08-30）：M2 实现 F-REPL-001~003，状态转 implemented，绑定 engine `>=0.3.0,<0.4.0`。

## 兼容矩阵

| 组件 | 版本 | 兼容要求 |
|---|---|---|
| warehouse-engine | 0.3.0 | 本 Skill 绑定 `>=0.3.0,<0.4.0` |
| contracts | 1.0 | 输入/输出 Schema 与 Pydantic 模型一致（`schema_version` 不变） |
| formula | 0.1.0 | 口径见 `docs/formula-spec.md` §7，已冻结 |
