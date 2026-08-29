# 仓库品类分析决策工具：M0 公式口径冻结文档

| 项 | 值 |
|---|---|
| 文档版本 | 0.1.0-draft |
| formula_version | `"0.1.0"` |
| 口径负责人 | 开发者 B |
| 走查会签 | 开发者 A（待签）、开发者 B（待签） |
| 状态 | M0 草案 |

**状态说明**：本文所有口径当前均为**草案**。经 A、B 共同走查并在下表签字后，全文口径整体转为**冻结**，`formula_version` 保持 `"0.1.0"`。未冻结口径不得写入默认 `AnalysisResult`，只能以实验输出存在（`experimental/` 路径，显式标注非默认）。冻结后的任何变更必须执行第 11 节流程。

| 走查方 | 签字 | 日期 |
|---|---|---|
| 开发者 A | （待签） | |
| 开发者 B | （待签） | |

**上位文档**：`开发规划与协作需求文档.md` V2.0、`开发需求-B引擎Skill.md` V1.0、`项目开发文档评审报告.md`（P0 第 2 项）。本文只冻结口径（公式、边界、Warning、验收容差），不规定实现；实现一致性以黄金数据测试为准。

## 1. 目标与范围

- 消除评审报告指出的期初库存、COGS、库龄、ABC、MAPE 五类口径歧义。
- 覆盖七类口径：KPI/COGS、ABC、库龄、呆滞、补货、预测与误差、行业基准。
- 每类口径给出：公式 ID、版本、状态、定义与公式、输入字段、边界规则、Warning/错误码、黄金数据要求。

## 2. 通用约定

### 2.1 期间与事件归属

- 分析期间左闭右开：`[start_date, end_date)`。事件按 `move_date`（`occurred_at` 按仓库时区折算的本地日期）归属：`start_date <= move_date < end_date` 计入本期。
- 事件重放顺序：同一 SKU 内按 `(move_date, occurred_at, event_id)` 升序。

### 2.2 数量与金额精度

- 数量：Decimal，scale ≤ 3；金额：Decimal，scale ≤ 2，币种默认 CNY。
- 中间计算不舍入；仅在指标输出时舍入：金额 HALF_UP 至 0.01，数量保留输入 scale，比例类指标保留 6 位小数。

### 2.3 事件方向（对余额的影响）

- 入库、退货：`+quantity`；出库、报废：`-quantity`。
- 调拨：同一 `event_id` 生成调出仓 `-quantity`、调入仓 `+quantity` 两条记录，SKU 净余额不变，不计入出库量。
- 退货计入入库方向，同时冲减期间出库量（见 F-KPI-003）。
- 盘点为校正事件（见 2.5）；冲销为反向事件（见 2.6）。

### 2.4 负库存

- 重放中任一时点、任一 `(sku_id, warehouse_id)` 余额 < 0：**不静默丢弃、不静默截断**。余额照常参与后续计算，并发 Warning `NEGATIVE_BALANCE`（severity=warning，非阻断，fields 含 sku_id、warehouse_id、首个负余额日期与数值）。COGS 的附加处理见 F-COGS-001。

### 2.5 盘点（校正事件）

- 盘点记录携带实盘数量（`on_hand_qty`）。重放至盘点日期时，将该 `(sku_id, warehouse_id)` 余额替换为实盘值；替换差额不参与"净入库"累计语义，但影响期初/期末余额与库龄观察点余额。

### 2.6 冲销（反向事件）

- 冲销事件引用被冲销事件的 `event_id`，方向与被冲销事件相反、数量相同，重放时等价于撤销原事件的库存影响。净入库、出库量、COGS 均按撤销后口径计算。
- 冲销引用的 `event_id` 不存在时，校验阶段报错误码 `DATA_VALIDATION_FAILED`（阻断，details 定位到事件）。

### 2.7 指标输出公共字段

每个指标输出必须携带以下字段，缺任一视为结果不完整：

- `formula_id`：本文定义的公式 ID；
- `formula_version`：当前 `"0.1.0"`；
- 数据期间：`[start_date, end_date)`；
- `sample_count`：样本数（SKU 数、事件数或需求期数，按各指标定义注明）。

### 2.8 Warning 结构

- Warning 五要素：`code`、`severity`、`message`（中文，仅展示）、`fields`、是否阻断。本文所有 Warning 均非阻断；UI 依据 `code` 而非文案判断。

## 3. KPI 与 COGS

公式组 `F-KPI`（001-008）、`F-COGS`（001）；版本 `"0.1.0"`；状态：草案（M0 拟冻结）。
组级输入字段：`sku_id`、`warehouse_id`、`move_type`、`quantity`、`move_date`、`unit_cost`、`lot_id`、`source`（lot_id 在 M0 不参与 KPI/COGS）。

### 3.1 F-KPI-001 期初库存

- 定义：期间开始前重放全部历史事件（`move_date < start_date`）得到的余额。
- 公式：`opening_qty(sku, wh) = Σ_{e: move_date(e) < start_date} dir(e) · qty(e)`；重放中遇盘点按 2.5 替换余额（下同）。无历史事件时为 0。
- 边界规则：SKU 级指标 = 各仓库分桶之和。
- Warning：无。
- 黄金数据：空历史、含盘点校正、含退货与冲销三类用例。

### 3.2 F-KPI-002 期末库存

- 公式：`closing_qty = opening_qty + Σ_{e ∈ 本期} dir(e) · qty(e)`（遇盘点替换余额）。
- 边界规则：`closing_qty` 允许为负，原样输出并发 `NEGATIVE_BALANCE`。
- 黄金数据：期末为 0、期末为负、期内含盘点三用例。

### 3.3 F-KPI-003 出库量

- 公式：`out_qty = Σ出库 + Σ报废 − Σ退货`（退货冲减出库量；调拨不计入）。
- 边界规则：`out_qty` 允许为负（期间退货大于出库的净退货场景），原样输出；仅用于日均出库量（3.8）、ABC 出库金额（第 4 节）等下游公式时按下限 0 取值。
- 黄金数据：纯出库、退货冲减至负、含报废三用例。

### 3.4 F-KPI-004 动销率

- 定义：期间有出库的 SKU 占参与统计 SKU 的比例。
- 公式：`active_ratio = |{sku : out_qty(sku) > 0}| / |{sku : closing_qty > 0 或 期间有事件}|`，`sample_count` = 分母 SKU 数。
- 边界规则：分母为 0（空数据集）时输出 null、`sample_count = 0`，不发 Warning。

### 3.5 F-KPI-005 库存价值

- 单位成本口径：取该 SKU 最近一次携带 `unit_cost` 的入库类事件（入库/退货，含期间开始前）的 `unit_cost`。
- 公式：`inventory_value = Σ_sku closing_qty(sku) × unit_cost(sku)`。
- 边界规则：`unit_cost` 缺失时，该 SKU 价值改用最近一条 `SnapshotRecord.inventory_value`（对应本地库 `stock_snapshot.inventory_value`）替代，并发 Warning `UNIT_COST_MISSING`（fields: sku_id）；两者均缺失时该 SKU 价值记 null、不计入合计，并发同一 Warning。
- 黄金数据：成本齐全、部分缺失走快照、全缺失三用例。

### 3.6 F-COGS-001 COGS

- 定义：期间销售成本。**M0 冻结口径（本文签字后生效）：移动加权平均成本法**；批次 FIFO 列为后续版本（须提升 formula_version），M0 不实现、不进默认结果。
- 公式（按 `(sku, wh)` 分桶逐事件重放）：
  - 入库/退货后：`avg_cost ← (balance_qty × avg_cost + in_qty × unit_cost) / (balance_qty + in_qty)`；
  - 出库/报废时：`cogs ← cogs + qty × avg_cost`；退货时：`cogs ← cogs − qty × avg_cost`；
  - `COGS = Σ期间出库成本 + Σ报废成本 − Σ退货冲减`。
- 边界规则：
  1. 期内任一时点余额 < 0：该 SKU 期间 COGS 记 0 并发 `NEGATIVE_BALANCE`；其他 SKU 不受影响。
  2. 出库时无任何已知成本记录（`unit_cost` 全缺）：该笔成本记 0 并发 `UNIT_COST_MISSING`。
  3. `balance_qty + in_qty = 0` 时不重算 `avg_cost`（跳过该次）。
- 黄金数据：多次入库不同成本、退货冲减、负库存、无成本记录四用例。

### 3.7 F-KPI-006 周转率 / F-KPI-007 周转天数

- 公式：`turnover = COGS / avg_inventory_value`，其中 `avg_inventory_value = (opening_value + closing_value) / 2`（金额口径；`opening_value` = 期初数量 × 3.5 单位成本口径）。
- `turnover_days = period_days / turnover`，`period_days = (end_date − start_date).days`。
- 边界规则：`avg_inventory_value <= 0` 或 turnover 为 null/≤0 时，两项输出 null 并在结果标注原因，不伪造 0。

### 3.8 F-KPI-008 覆盖天数

- 公式：`daily_out = max(0, out_qty) / period_days`；`coverage_days = closing_qty / daily_out`。
- 边界规则：`closing_qty <= 0` 或 `daily_out = 0`（期间无出库）时输出 null。
- 黄金数据（3.7/3.8 共用）：平均库存为 0、无出库、正常周转三用例。

## 4. ABC 分类

### F-ABC-001 期间 ABC 分类

- 版本：`"0.1.0"`；状态：草案（M0 拟冻结）。
- 排序指标：期间出库金额 `amt(sku) = max(0, out_qty) × unit_cost(sku)`（单位成本口径同 3.5）。
- 排序规则：`amt` 降序；`amt` 并列时按 `sku_id` 字典序升序（稳定排序，同输入必同输出）。
- 归属规则（左闭右开衔接，互斥完备）：排序后累计 `cum_i = (Σ_{j≤i} amt_j) / Σ amt`，则
  - `cum_i ≤ 0.80` → A；`0.80 < cum_i ≤ 0.95` → B；`cum_i > 0.95` → C。
  - 边界值归属：恰为 80.00% 归 A、恰为 95.00% 归 B；下一档不含上一档右边界（B 不含 0.80、C 不含 0.95），各 SKU 恰归一档。
- 输入字段：`sku_id`、`move_type`、`quantity`、`move_date`、`unit_cost`。
- 边界规则：
  1. `amt = 0` 的 SKU（含净退货为负、按下限 0 者）一律归 C，并发 Warning `NO_OUTFLOW`（fields: sku_id）。
  2. `Σ amt = 0`（全部 SKU 无出库）：ABC 整体不可计算，全部 SKU 归 C 并发 `NO_OUTFLOW`，结果标注"分母为 0"。
- 黄金数据：累计占比恰为 80%/95% 边界、`amt` 并列、零出库、全零四用例；分类与排序必须与期望完全一致。

## 5. 库龄

### F-AGE-001 期末库龄分布

- 版本：`"0.1.0"`；状态：草案（M0 拟冻结）。
- 观察点：期末（`end_date` 时点）；`age = (观察点日期 − 入库日期).days`。
- 事件口径：
  - 有 `lot_id`：期末余额按 `(sku, wh, lot)` 分桶，各桶库龄 = 观察点 − 该 lot 最近一次入库 `move_date`。
  - 无批次：按平均成本法近似——以构成期末余额的入库事件为总体，`avg_age = Σ(qty_i × age_i) / Σ qty_i`，SKU 记单一平均库龄并在结果标注"近似"；构成不足时回退到全历史入库事件。
- 区间（左闭右开，各桶恰归一档）：`[0,30)`、`[30,60)`、`[60,90)`、`[90,180)`、`[180,+∞)`。边界值恰为 30 天归 `[30,60)`，其余同理。
- 边界规则：
  1. `move_date` / `occurred_at` 缺失或非法：发 Warning `DATE_MISSING`（fields: event_id、sku_id），**跳过该记录**，不猜测、不静默修正日期。
  2. 期末库存为 0 的 SKU 不参与库龄分布。
  3. 全历史无入库事件但期末库存 > 0：该 SKU 跳过并发 `DATE_MISSING`（fields 标注原因 no_inbound_event）。
- 输出：各档位数量与金额分布（金额用 3.5 单位成本口径）；`sample_count` = 参与桶数。
- 黄金数据：库龄恰为 0/30/60/90/180 天边界、缺日期、无批次近似三组用例。

## 6. 呆滞

### F-STALE-001 呆滞判定

- 版本：`"0.1.0"`；状态：草案（M0 拟冻结）。
- 参数与默认值（均可在 `AnalysisRequest` 配置）：

| 参数 | 含义 | 默认值 |
|---|---|---|
| `stale_window_days` | 观察窗口天数 | 90 |
| `min_stock_qty` | 期末库存最低阈值 | 1 |
| `new_inbound_grace_days`（X） | 新入库豁免天数 | 30 |
| `discontinued_flag` | 停产标记（按 SKU 传入） | false |

- 观察窗口 = `[end_date − stale_window_days, end_date)`。
- 判定公式：`呆滞 ⟺ no_outflow_days > stale_window_days 且 closing_qty > min_stock_qty 且不满足任一排除条件`。
- `no_outflow_days = (观察点 − 最后出库日期).days`；无任何出库记录时取 `(观察点 − 首次入库日期).days`。
- 排除条件：
  1. 新入库 SKU：首次入库日期晚于（窗口起点 − `new_inbound_grace_days` 天），即入库发生在"窗口起点前 X 天"至观察点之间（含窗口内新入库）的 SKU 视为新品，不判呆滞。
  2. 停产标记：`discontinued_flag = true` 的 SKU 不判呆滞（其库龄与金额照常输出）。
- 输出：呆滞 SKU 清单（含 `no_outflow_days`、`closing_qty`）、呆滞数量、呆滞金额（`closing_qty` × 3.5 单位成本）、呆滞率 = 呆滞金额 / 期末库存总价值。被排除 SKU 在结果标注 `excluded_reason`，不发 Warning。
- 黄金数据：`no_outflow_days` = 90（非呆滞）与 91（呆滞）、`closing_qty` = 1（非呆滞）、新品豁免边界、停产排除五用例。

## 7. 补货

公式组 `F-REPL`（001-003）；版本 `"0.1.0"`；状态：草案（M0 拟冻结）。
组级输入字段：`sku_id`、`quantity`、`move_date`、`move_type`、参数 `lead_time_days`、`service_level`、`on_order_qty`。

### 7.1 F-REPL-001 安全库存

- 公式：`SS = z × σ_d × √LT`。
- `σ_d`：期间内逐日出库量的样本标准差（n−1 分母，含零出库日；日需求取 `max(0, out_qty)` 的日聚合）。
- `LT = lead_time_days`（非负 Decimal）。

### 7.2 F-REPL-002 补货点

- 公式：`ROP = d̄ × LT + SS`；`d̄ = max(0, out_qty) / period_days`（日均出库量）。

### 7.3 F-REPL-003 建议补货量

- 公式：`Q* = max(0, ROP − 当前库存 − 在途量)`。
- 当前库存 = `closing_qty`（F-KPI-002）；在途量 = `on_order_qty`（未到货采购量），缺省 0。

### 7.4 服务水平与 z 值

- `service_level` 默认 0.95（正态假设），z 值表内置：0.90→1.282、0.95→1.645、0.98→2.054、0.99→2.326；表外值取最近档位并在结果标注实际使用的 z 与 `service_level`。

### 7.5 参数缺失与边界

- `lead_time_days` 或 `service_level` 任一缺失：返回状态码 `PARAM_MISSING`（非阻断 Warning，fields: sku_id、缺失参数名），该 SKU 不输出补货建议（SS/ROP/Q* 均为 null），其余分析继续。
- 边界：`σ_d = 0` → `SS = 0`；`d̄ = 0` → `ROP = SS`；均照常输出。
- 黄金数据：z 表四档、参数缺失、零需求、在途量扣减四组用例。

## 8. 预测与误差

公式组 `F-FCST`（001-002）；版本 `"0.1.0"`；状态：草案（M0 拟冻结）。

### 8.1 F-FCST-001 默认预测模型

- 默认模型族为"季节性朴素 / 移动平均基线"，**M0 冻结为 4 周移动平均基线**（季节性朴素列为后续候选）。需求按自然周聚合（周一为周首），下一周预测 = 最近 4 周需求算术平均：`ŵ_{K+1} = (w_K + w_{K−1} + w_{K−2} + w_{K−3}) / 4`；日均预测 = 周预测 / 7。
- statsmodels 模型（Holt-Winters、ARIMA 等）为 M2 实验内容，位于 `experimental/`，不进默认 `AnalysisResult`。
- 训练/预测窗口切分：**按时间顺序切分，禁止随机打乱**。训练窗口 `[start_date, split_date)`，预测窗口 `[split_date, end_date)`；`split_date` 由请求传入，缺省 = `end_date − 28 天`。

### 8.2 F-FCST-002 误差指标

在预测窗口上计算：`d_t` 实际需求、`f̂_t` 预测值、`e_t = d_t − f̂_t`、`n` 为预测窗口期数：

- `MAPE = (100% / n′) × Σ_{d_t > 0} |e_t| / d_t`（`d_t = 0` 的期不计入；`n′` 为 `d_t > 0` 的期数；`n′ = 0` 时 MAPE 输出 null）。
- `WAPE = Σ|e_t| / Σ d_t`（`Σ d_t = 0` 时输出 null；作为 MAPE 不可计算时的补充指标）。
- `MAE = (1/n) × Σ|e_t|`；`RMSE = √((1/n) × Σ e_t²)`。

### 8.3 样本不足判定

- 有效需求期数 `n < 12`：强制降级为基线模型（即使请求了实验模型），发 Warning `INSUFFICIENT_SAMPLES`（fields: sku_id、n）；误差指标照常输出，但结果标注"样本不足，不参与达标判定"。

### 8.4 验收口径

- 禁止使用"误差为 0"或"MAPE ≤ 25%"作为验收标准。验收改为声明式：在黄金数据集 `<dataset-version>`（SKU 清单、期间、样本量固定）与 CI 硬件条件下，MAPE/MAE/RMSE 与黄金值的偏差不超过第 10 节声明容差。
- 黄金数据：≥12 期周需求（含季节性与零需求期）、`n < 12`、切分边界三组用例。

## 9. 行业基准

### F-BM-001 行业基准比较

- 版本：`"0.1.0"`；状态：草案（M0 拟冻结）。
- 基准记录必备字段：`source`（来源）、`region`（地区）、`industry`（行业）、`sample_scope`（样本范围）、`updated_at`（更新时间）、`benchmark_version`（版本）、`unit`（指标单位）、`applicability`（适用限制）。任一字段缺失的基准记录不得使用。
- 匹配规则：按 `(industry, region, 指标)` 匹配；`region` 缺省取全国口径；多条命中取 `benchmark_version` 与 `updated_at` 最新者。
- 无匹配：返回 `BENCHMARK_UNAVAILABLE`（非阻断），不输出任何比较结论。**禁止将无来源说明的经验数字作为默认基准或兜底值。**
- 输出：商户指标值、基准值、绝对/相对差异、基准完整元数据（上述八字段）；基准数据集随 fixture 版本化。
- 黄金数据：有匹配、无匹配（`BENCHMARK_UNAVAILABLE`）两用例。

## 10. 黄金数据与容差总表

黄金数据位置：`fixtures/golden/<dataset-version>/input.json` + `expected.json`，含预期数值、分类、排序、Warning 与容差声明。

| 指标类 | 判定方式 | 容差（绝对 abs / 相对 rel） |
|---|---|---|
| KPI 金额类（库存价值、COGS、呆滞金额、出库金额） | 数值容差 | abs ≤ 0.01 或 rel ≤ 1e-6（满足其一即通过） |
| KPI 数量类（期初/期末/出库量、SS/ROP/Q*） | 数值容差 | abs ≤ 0.001 或 rel ≤ 1e-9 |
| KPI 比率/天数类（动销率、周转率、周转天数、覆盖天数） | 数值容差 | abs ≤ 1e-6 或 rel ≤ 1e-6 |
| ABC 分类与排序 | 完全一致 | 分类、排序序号必须与期望完全一致；`amt` 数值走金额容差 |
| 库龄 | 档位完全一致 | 档位归属必须完全一致；平均库龄数值 abs ≤ 0.01 天 |
| 呆滞 | 结论完全一致 | 判定结论与 `excluded_reason` 完全一致；金额走金额容差 |
| 预测值与 MAPE/MAE/RMSE/WAPE | 声明式验收 | 在黄金数据集版本、样本量与 CI 硬件条件下，与黄金值偏差 abs ≤ 1e-6 或 rel ≤ 1e-6；确定性路径应字节级一致 |
| Warning | 完全一致 | `code` 集合与 `fields` 逐字段一致（不比较中文 message） |

- 重复运行要求：同一输入、同一 contracts 与 `formula_version` 下重复运行，结果字节级稳定，或差异在上表容差内。

## 11. 变更规则

任何口径变更（公式、边界规则、参数默认值、Warning 语义、容差）必须：

1. 提升 `formula_version`：不兼容变更升次版本（如 `0.1.0 → 0.2.0`）；澄清且不影响数值结果的变更升修订号（如 `0.1.1`），并在变更日志说明为何不属不兼容变更。
2. 重新生成黄金结果：更新 `fixtures/golden/<dataset-version>/expected.json`；CI 中新旧版本黄金数据并行运行至少一个发布周期。
3. 说明对旧报告的影响：历史 `AnalysisResult` 不追溯重算；重开旧报告时按其自身 `formula_version` 对应口径执行，并在 UI 标注"口径版本低于当前版本"。

未冻结口径（批次 FIFO COGS、statsmodels 实验模型等）不得进入默认 `AnalysisResult`；转入默认前必须先在本文冻结并执行上述变更流程。
