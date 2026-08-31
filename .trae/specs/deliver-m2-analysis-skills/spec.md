# 开发者 B：M2 分析 Skill Spec

## Why

M1 的 KPI/COGS 引擎已交付（engine 0.2.0，B 侧 147 个测试全绿），`docs/formula-spec.md` 0.1.0 口径已由 A、B 双方签字冻结。按 `开发需求-B引擎Skill.md` §7 M2，引擎需从"仅 KPI 真实计算 + 四类占位"升级为"五类分析能力全部真实计算"：ABC/库龄/呆滞、补货、预测与误差评估、行业基准。

当前 `calculators/abc_aging.py`、`replenishment.py`、`forecasting.py`、`benchmark_compare.py` 四个模块仍是 `raise NotImplementedError` 存根，`WarehouseEngine.analyze` 只对 KPI 返回真实结果并附一条 `ANALYSIS_PLACEHOLDER`。`replay.py` 重放内核亦未暴露 M2 所需的逐日净出库序列、批次级期末余额、末次出库/首次入库日期与构成期末余额的入库事件。

## What Changes

- **分支**：从 `origin/master`（d7e1468）创建 `feature/b-m2-analysis-skills`。
- **重放内核扩展**（`replay.py`）：追加 M2 采集项——逐日净出库序列（补货 σ_d）、批次级期末余额与 FIFO 残余入库切片（库龄）、末次出库/首次入库日期（呆滞）。全部为带默认值的追加字段，`buckets` 内容与顺序零变化，M1 既有 147 个测试不受影响。
- **四个计算器实装**：`F-ABC-001`/`F-AGE-001`/`F-STALE-001`、`F-REPL-001~003`、`F-FCST-001~002`、`F-BM-001`，口径严格按 `docs/formula-spec.md` §4~§9 冻结定义。
- **analyze() 全量接线**：`metrics` 由 9 个增至 18 个（KPI 9 + ABC/库龄/呆滞 3 + 补货 3 + 预测 2 + 基准 1），**移除 `ANALYSIS_PLACEHOLDER` 及全部占位措辞**。
- **实验模型隔离骨架**：新增 `experimental/`（类型层 + 导入层双重隔离），不进默认 `AnalysisResult`；新增测试断言默认路径不 import `statsmodels`。
- **黄金数据**：新增 `tests/fixtures/golden/v0.3.0/`（ABC/库龄/呆滞边界）与 `v0.4.0/`（补货/预测/基准），手工推导 + `derivation` 字段；回填 `v0.1.0`/`v0.2.0` 的新增指标与 Warning。
- **文档与清单**：四个 SKILL.md 由 skeleton 转 implemented、`manifest.json`、`CHANGELOG.md` 0.3.0、`compatibility-matrix.md`、新增 `docs/m2-handover-b.md`。

**契约变更：无。** `schema_version` 保持 `1.0`，`packages/contracts-python/` 与 `packages/contracts-schema/` 零改动。M2 结果**仅数据集级聚合进 `AnalysisResult.metrics`**，per-SKU 明细留在各计算器返回的 dataclass 内供测试断言，不对外暴露。`engine` 0.2.0 → 0.3.0，`formula_version` 保持 0.1.0。

## Impact

- Affected specs: `deliver-m1-kpi-engine`（其"其余四类维持占位"的临时行为由本 spec 终结）。
- Affected code: `packages/warehouse-engine/src/warehouse_engine/`（`replay.py`、`calculators/{abc_aging,replenishment,forecasting,benchmark_compare}.py`、`engine.py`、`__version__.py`、新增 `experimental/`）、`tests/`（新增 7 个测试文件、2 版黄金数据集、6 份边界 fixture、回填 2 版旧 expected、改 `test_analyze_integration.py` 与 `test_golden.py`）、`skills/`（四个 SKILL.md、manifest.json）、`docs/`（m2-handover-b.md、compatibility-matrix.md）、`CHANGELOG.md`。
- **不修改 A 侧代码**（`services/`、`apps/`、`local-data/`）——`"导入→分析→报告"` 联调以 B 侧 `tests/engine/test_m2_integration.py` 自证，待 A 侧确认项写入 `docs/m2-handover-b.md` §8。

## ADDED Requirements

### Requirement: ABC 分类（F-ABC-001）

系统 SHALL 按期间出库金额降序累计占比对 SKU 做 A/B/C 分层，`cum_i ≤ 0.80 → A`、`0.80 < cum_i ≤ 0.95 → B`、`cum_i > 0.95 → C`，各 SKU 恰归一档。

#### Scenario: 累计占比恰为 80% 与 95% 边界
- **WHEN** 某 SKU 排序后累计占比恰为 0.80 或 0.95
- **THEN** 恰为 0.80 归 A、恰为 0.95 归 B；下一档不含上一档右边界

#### Scenario: 出库金额并列
- **WHEN** 多个 SKU 的 `amt` 完全相同
- **THEN** 按 `sku_id` 字典序升序稳定排序，同输入必同输出

#### Scenario: 零出库与全零
- **WHEN** 某 SKU `amt = 0`（含净退货为负按下限 0 者）
- **THEN** 一律归 C 并发 `NO_OUTFLOW`（fields 含 sku_id）；`Σ amt = 0` 时全部归 C，每个 SKU 各发一条 `NO_OUTFLOW`，分类指标标注 reason=`zero_total_amount`

### Requirement: 库龄分布（F-AGE-001）

系统 SHALL 以 `end_date` 为观察点计算期末库龄，区间左闭右开 `[0,30) [30,60) [60,90) [90,180) [180,+∞)`，各桶恰归一档。

#### Scenario: 区间边界归属
- **WHEN** 库龄恰为 30 / 60 / 90 / 180 天
- **THEN** 分别归入 `[30,60)` / `[60,90)` / `[90,180)` / `[180,+∞)`

#### Scenario: 无批次近似
- **WHEN** 事件无 `lot_id`
- **THEN** 以构成期末余额的入库事件为总体按 `Σ(qty_i×age_i)/Σ qty_i` 计算平均库龄，结果标注"近似"；构成不足（盘点/负库存/冲销导致 `Σ剩余 ≠ closing_qty`）回退全历史入库事件并标注 `age_basis`

#### Scenario: 期末有库存但全历史无入库事件
- **WHEN** 某 SKU 期末余额 > 0 但全历史无入库类事件
- **THEN** 该 SKU 跳过库龄分桶并发 `DATE_MISSING`（fields 含 sku_id 与 `no_inbound_event`），不猜测、不静默修正

### Requirement: 呆滞判定（F-STALE-001）

系统 SHALL 按 `no_outflow_days > stale_window_days 且 closing_qty > min_stock_qty 且不满足任一排除条件` 判定呆滞。

#### Scenario: 无出库天数边界
- **WHEN** `no_outflow_days` 为 90 与 91（观察窗口 90 天）
- **THEN** 90 判为非呆滞、91 判为呆滞（严格大于）

#### Scenario: 库存阈值边界
- **WHEN** `closing_qty` 恰等于 `min_stock_qty`（默认 1）
- **THEN** 判为非呆滞（严格大于）

#### Scenario: 排除条件
- **WHEN** SKU 首次入库晚于 `窗口起点 − new_inbound_grace_days`，或被标记停产
- **THEN** 不判呆滞，在结果标注 `excluded_reason`（`new_inbound` / `discontinued` / `below_min_stock`），**不发 Warning**

### Requirement: 补货计算（F-REPL-001~003）

系统 SHALL 按 `SS = z·σ_d·√LT`、`ROP = d̄·LT + SS`、`Q* = max(0, ROP − closing_qty − on_order_qty)` 计算，σ_d 为期间逐日净出库样本标准差（n−1 分母，含零出库日）。

#### Scenario: z 值表四档与表外值
- **WHEN** `service_level` 为 0.90/0.95/0.98/0.99
- **THEN** 分别取 z = 1.282/1.645/2.054/2.326；表外值取最近档位并在结果标注实际使用的 z，差值相等时取较低档（确定性）

#### Scenario: 参数缺失
- **WHEN** 某 SKU 的 `lead_time_days` 或 `service_level` 缺失且无默认值可回退
- **THEN** 发非阻断 `PARAM_MISSING`（fields 含 sku_id 与缺失参数名），该 SKU 的 SS/ROP/Q* 均为 null，其余分析继续

#### Scenario: 零需求与在途量
- **WHEN** `σ_d = 0` 或 `d̄ = 0`
- **THEN** `SS = 0`、`ROP = SS`，照常输出；`on_order_qty > 0` 时从 Q* 中扣减，结果按下限 0 截断

### Requirement: 预测与误差评估（F-FCST-001~002）

系统 SHALL 以 4 周移动平均为默认基线模型（按自然周聚合、周一为周首），按时间顺序切分训练/预测窗口，并在预测窗口上计算 MAPE/WAPE/MAE/RMSE。

#### Scenario: 窗口切分
- **WHEN** 未显式传入 `split_date`
- **THEN** 取 `split_date = end_date − 28 天`；训练窗口 `[start_date, split_date)`、预测窗口 `[split_date, end_date)`，**禁止随机打乱**

#### Scenario: 样本不足降级
- **WHEN** 预测窗口有效需求期数 `n < 12`
- **THEN** 强制降级为基线模型并发 `INSUFFICIENT_SAMPLES`（fields 含 sku_id 与 n），误差指标照常输出并标注"样本不足，不参与达标判定"

#### Scenario: MAPE 不可计算
- **WHEN** 预测窗口内实际需求全为 0（`n′ = 0`）
- **THEN** MAPE 输出 null 并标注 reason，以 WAPE 作为补充指标；`Σd_t = 0` 时 WAPE 亦为 null

### Requirement: 行业基准比较（F-BM-001）

系统 SHALL 按 `(industry, region, metric_name)` 匹配基准，多条命中取 `benchmark_version` 与 `updated_at` 最新者；无匹配返回 `BENCHMARK_UNAVAILABLE`，**禁止将无来源说明的经验数字作为默认基准或兜底值**。

#### Scenario: 基准元数据不完整
- **WHEN** 某条基准记录缺少 `source`/`region`/`industry`/`sample_scope`/`updated_at`/`benchmark_version`/`unit`/`applicability` 中任一字段
- **THEN** 该记录直接丢弃，不得参与匹配

#### Scenario: 无匹配基准
- **WHEN** 商户行业/地区无对应基准记录
- **THEN** 发非阻断 `BENCHMARK_UNAVAILABLE`，不输出任何比较结论，比较指标为 null 并标注 reason=`benchmark_unavailable`

### Requirement: 实验模型隔离

实验模型 SHALL 位于 `experimental/`，通过类型层（独立结果类型，不继承亦不转换为 `ResultMetric`）与导入层（`engine.py` 与四个计算器均不 import `experimental`）双重隔离，不进默认 `AnalysisResult`。

#### Scenario: 默认路径不含实验输出
- **WHEN** 执行 `analyze()` 或 `list_capabilities()`
- **THEN** 结果中不含任何实验模型输出，`list_capabilities` 的 `formula_ids` 不含实验模型，且 `sys.modules` 中不含 `statsmodels`

## MODIFIED Requirements

### Requirement: analyze 返回占位结果（M1 临时行为）

M1 的 `analyze` 对 abc-aging/replenishment/forecasting/benchmark 四类返回 `ANALYSIS_PLACEHOLDER` 占位。M2 起该行为**完全移除**：五类公式（18 个指标）全部返回真实计算结果，结果中不再出现 `ANALYSIS_PLACEHOLDER`。
**Migration**: 契约结构不变（仍为 `AnalysisResult.metrics` + `warnings` + `data_quality`），A 侧仅新增 9 个指标名可消费；占位 Warning 消失后 A 侧若依赖该码做展示需同步调整（写入 m2-handover-b.md §8 协调点）。

## REMOVED Requirements

- `ANALYSIS_PLACEHOLDER` Warning（随四类计算器实装而移除，不再作为任何能力的降级标识）。
