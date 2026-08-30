# Checklist：开发者 B M2 分析 Skill

## 分支与基线
- [x] `feature/b-m2-analysis-skills` 分支从 `origin/master`（d7e1468）创建，本地 master 未被直接提交
- [x] `uv sync --all-packages --group dev` 后 M1 基线复现：`pytest tests/engine tests/contract` → 147 passed
- [x] 未修改 A 侧代码（`services/`、`apps/`、`local-data/` 零改动；联调以 B 侧集成测试自证）

## 重放内核扩展
- [x] `daily_out_by_sku` 覆盖 `[start_date, end_date)` 每一天，零出库日补 0，按日期升序
- [x] `last_outbound_date_by_sku` / `first_inbound_date_by_sku` 覆盖全重放视界（含历史）
- [x] `lot_balances` 为 FIFO 消耗后的期末批次余额，按 `(sku_id, warehouse_id, lot_id)` 字典序
- [x] `residual_inbound_by_sku` 与 `all_inbound_by_sku` 的回退条件有测试覆盖
- [x] M1 既有 147 个测试无回归（`buckets` 内容与顺序零变化；扩展后 273 passed / 17 skipped）

## ABC / 库龄 / 呆滞
- [x] 累计占比恰为 80% / 95% 归属正确（80 归 A、95 归 B）
- [x] `amt` 并列按 `sku_id` 字典序稳定排序，同输入必同输出
- [x] `amt = 0` 归 C 并发 `NO_OUTFLOW`；`Σ amt = 0` 全部归 C 且每 SKU 各一条
- [x] 库龄区间左闭右开，30/60/90/180 天边界归属正确
- [x] 无批次走 FIFO 平均库龄；构成不足回退全历史并标注 `all_history`
- [x] 期末 > 0 但无入库事件 → 跳过 + `DATE_MISSING`（fields 含 `no_inbound_event`）
- [x] 呆滞 90 天非呆滞 / 91 天呆滞；`closing_qty = min_stock_qty` 非呆滞
- [x] 新品豁免优先于天数判定、停产排除标 `excluded_reason`，不发 Warning

## 补货
- [x] `SS = z·σ_d·√LT`，σ_d 为样本标准差（n−1，含零出库日），`√` 用 `Decimal.sqrt()`
- [x] z 表四档正确（0.90→1.282、0.95→1.645、0.98→2.054、0.99→2.326）；表外取最近档，差值相等取较低档
- [x] `ROP = d̄·LT + SS`、`Q* = max(0, ROP − closing_qty − on_order_qty)`
- [x] `σ_d = 0 → SS = 0`、`d̄ = 0 → ROP = SS` 照常输出
- [x] `lead_time_days` 缺失 → 非阻断 `PARAM_MISSING`（fields 含 sku_id 与参数名），该 SKU 三项 null

## 预测
- [x] 自然周聚合（周一为周首），首尾不完整周照常参与
- [x] `split_date` 缺省 `end_date − 28 天`；训练/预测窗口按时间顺序切分，不随机打乱
- [x] 4 周移动平均基线；日均 = 周预测 / 7
- [x] MAPE（`d_t = 0` 期不计入）、WAPE、MAE、RMSE 四项按 §8.2 口径
- [x] `n < 12`（全期间自然周数）强制降级基线 + `INSUFFICIENT_SAMPLES`（fields 含 sku_id 与 n）

## 基准
- [x] 基准八字段缺一即丢弃，不参与匹配
- [x] 多条命中取 `benchmark_version` 与 `updated_at` 最新者，并列按 `source` 字典序
- [x] 无匹配 → 非阻断 `BENCHMARK_UNAVAILABLE`，不输出比较结论，无无来源兜底值

## 实验模型隔离
- [x] `experimental/` 使用独立结果类型，不继承亦不转换为 `ResultMetric`
- [x] `engine.py` 与四个计算器均不 import `experimental`
- [x] 默认 `analyze()` / `list_capabilities()` 结果不含实验输出，且默认路径不 import `statsmodels`（子进程断言）

## analyze 集成
- [x] 18 个指标齐全（KPI 9 + ABC/库龄/呆滞 3 + 补货 3 + 预测 2 + 基准 1），`formula_id` 唯一
- [x] 结果中不再出现 `ANALYSIS_PLACEHOLDER`
- [x] `engine_version` = 0.3.0，`formula_version` = 0.1.0（口径未变更）
- [x] 确定性：同一输入两次 `analyze` 的 `model_dump_json()` 逐字节一致（M1 测试继承）
- [x] 数据质量报告按码汇总（计数 + 明细），覆盖新增 Warning 码

## 黄金数据
- [x] `golden/v0.3.0/` 覆盖 ABC/库龄/呆滞全部声明边界（手工推导 + derivation，禁止引擎回填）
- [x] `golden/v0.4.0/` 覆盖补货/预测/基准全部声明边界
- [x] `v0.1.0` / `v0.2.0` 的 `metrics` 与 `analyze_warnings` 已回填，四版本并行运行
- [x] 既有 5 份边界 fixture 的 analyze_warnings 已更新为 M2 真实告警（NO_OUTFLOW/PARAM_MISSING/INSUFFICIENT_SAMPLES/BENCHMARK_UNAVAILABLE），逐条手验；M2 关键边界另由 `test_abc_aging`/`test_replenishment`/`test_forecasting`/`test_benchmark_compare` 覆盖
- [x] `test_analyze_integration.py` 自证"导入→分析→报告"链路（18 指标 + 移除占位 + 数据质量报告，未改 A 侧代码）

## 交付物
- [x] 四个 SKILL.md 转 implemented（版本 0.3.0、engine 绑定 `>=0.3.0,<0.4.0`）
- [x] `skills/manifest.json` 同步
- [x] `CHANGELOG.md` 0.3.0（Added / Changed / 说明）
- [x] `docs/compatibility-matrix.md` 同步 engine 0.3.0 与 Skill bundle 0.3.0
- [x] `docs/m2-handover-b.md` 含安装命令、新增指标映射、待 A 复核决策、已知限制、与 A 协调点

## 验收
- [x] `pytest` 全绿（B 侧 273 passed / 17 skipped，A 侧未触碰）
- [x] `ruff check packages scripts tests` 全过
- [x] `mypy packages/warehouse-engine/src packages/contracts-python/src` 无问题
- [ ] `scripts/export_schemas.py` 重导出无 diff（**契约零改动门禁证据**）—— Task 10 复测
- [ ] `perf_bench` 1 万/10 万行未劣化 M1 阈值（≤5s / ≤40s）—— Task 10 复测
- [ ] PR 按 §26.2 模板提交，contracts 无变更（schema_version 保持 1.0）
