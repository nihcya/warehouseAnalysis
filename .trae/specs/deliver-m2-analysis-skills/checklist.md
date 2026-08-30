# Checklist：开发者 B M2 分析 Skill

## 分支与基线
- [x] `feature/b-m2-analysis-skills` 分支从 `origin/master`（d7e1468）创建，本地 master 未被直接提交
- [x] `uv sync --all-packages --group dev` 后 M1 基线复现：`pytest tests/engine tests/contract` → 147 passed
- [ ] 未修改 A 侧代码（`services/`、`apps/`、`local-data/`）

## 重放内核扩展
- [ ] `daily_out_by_sku` 覆盖 `[start_date, end_date)` 每一天，零出库日补 0，按日期升序
- [ ] `last_outbound_date_by_sku` / `first_inbound_date_by_sku` 覆盖全重放视界（含历史）
- [ ] `lot_balances` 为 FIFO 消耗后的期末批次余额，按 `(sku_id, warehouse_id, lot_id)` 字典序
- [ ] `residual_inbound_by_sku` 与 `all_inbound_by_sku` 的回退条件有测试覆盖
- [ ] M1 既有 147 个测试无回归（`buckets` 内容与顺序零变化）

## ABC / 库龄 / 呆滞
- [ ] 累计占比恰为 80% / 95% 归属正确（80 归 A、95 归 B）
- [ ] `amt` 并列按 `sku_id` 字典序稳定排序，同输入必同输出
- [ ] `amt = 0` 归 C 并发 `NO_OUTFLOW`；`Σ amt = 0` 全部归 C 且每 SKU 各一条
- [ ] 库龄区间左闭右开，30/60/90/180 天边界归属正确
- [ ] 无批次走 FIFO 平均库龄并标注"近似"；构成不足回退全历史并标注 `age_basis`
- [ ] 期末 > 0 但无入库事件 → 跳过 + `DATE_MISSING`（fields 含 `no_inbound_event`）
- [ ] 呆滞 90 天非呆滞 / 91 天呆滞；`closing_qty = min_stock_qty` 非呆滞
- [ ] 新品豁免与停产排除标 `excluded_reason`，不发 Warning

## 补货
- [ ] `SS = z·σ_d·√LT`，σ_d 为样本标准差（n−1，含零出库日），`√` 用 `Decimal.sqrt()`
- [ ] z 表四档正确（0.90→1.282、0.95→1.645、0.98→2.054、0.99→2.326）；表外取最近档，差值相等取较低档
- [ ] `ROP = d̄·LT + SS`、`Q* = max(0, ROP − closing_qty − on_order_qty)`
- [ ] `σ_d = 0 → SS = 0`、`d̄ = 0 → ROP = SS` 照常输出
- [ ] `lead_time_days` / `service_level` 缺失 → 非阻断 `PARAM_MISSING`（fields 含 sku_id 与参数名），该 SKU 三项 null

## 预测
- [ ] 自然周聚合（周一为周首），首尾不完整周照常参与
- [ ] `split_date` 缺省 `end_date − 28 天`；训练/预测窗口按时间顺序切分，不随机打乱
- [ ] 4 周移动平均基线 `ŵ_{K+1} = (w_K+…+w_{K−3})/4`；日均 = 周预测 / 7
- [ ] MAPE（`d_t = 0` 期不计入）、WAPE、MAE、RMSE 四项按 §8.2 口径
- [ ] `n < 12` 强制降级基线 + `INSUFFICIENT_SAMPLES`（fields 含 sku_id 与 n）

## 基准
- [ ] 基准八字段缺一即丢弃，不参与匹配
- [ ] 多条命中取 `benchmark_version` 与 `updated_at` 最新者，并列按 `source` 字典序
- [ ] 无匹配 → 非阻断 `BENCHMARK_UNAVAILABLE`，不输出比较结论，无无来源兜底值

## 实验模型隔离
- [ ] `experimental/` 使用独立结果类型，不继承亦不转换为 `ResultMetric`
- [ ] `engine.py` 与四个计算器均不 import `experimental`
- [ ] 默认 `analyze()` / `list_capabilities()` 结果不含实验输出，且默认路径不 import `statsmodels`

## analyze 集成
- [ ] 18 个指标齐全（KPI 9 + ABC/库龄/呆滞 3 + 补货 3 + 预测 2 + 基准 1），`formula_id` 唯一
- [ ] 结果中不再出现 `ANALYSIS_PLACEHOLDER`
- [ ] `engine_version` = 0.3.0，`formula_version` = 0.1.0（口径未变更）
- [ ] 确定性：同一输入两次 `analyze` 的 `model_dump_json()` 逐字节一致
- [ ] 数据质量报告按码汇总（计数 + 明细），覆盖新增 Warning 码

## 黄金数据
- [ ] `golden/v0.3.0/` 覆盖 ABC/库龄/呆滞全部声明边界（手工推导 + derivation，禁止引擎回填）
- [ ] `golden/v0.4.0/` 覆盖补货/预测/基准全部声明边界
- [ ] `v0.1.0` / `v0.2.0` 的 `metrics` 与 `analyze_warnings` 已回填，双版本并行运行
- [ ] 6 份 M2 edge fixture 已登记进 `test_edge_cases.py` 且断言通过
- [ ] `test_m2_integration.py` 自证"导入→分析→报告"链路（未改 A 侧代码）

## 交付物
- [ ] 四个 SKILL.md 转 implemented（版本 0.2.0、engine 绑定 `>=0.3.0,<0.4.0`）
- [ ] `skills/manifest.json` 同步
- [ ] `CHANGELOG.md` 0.3.0（Added / Changed / Known limitations）
- [ ] `docs/compatibility-matrix.md` 同步 engine 0.3.0 与 Skill bundle 0.3.0
- [ ] `docs/m2-handover-b.md` 含安装命令、新增指标映射、已知限制、回滚版本、与 A 的协调点

## 验收
- [ ] `pytest` 全绿（B 侧无回归，A 侧 47 个测试不回归）
- [ ] `ruff check packages scripts tests services local-data apps` 全过
- [ ] `mypy packages/warehouse-engine/src packages/contracts-python/src` 无问题
- [ ] `scripts/export_schemas.py` 重导出无 diff（**契约零改动门禁证据**）
- [ ] `perf_bench` 1 万/10 万行未劣化 M1 阈值（≤5s / ≤40s）
- [ ] PR 按 §26.2 模板提交，contracts 无变更（schema_version 保持 1.0）
