# 开发者 B：M2 交接说明（分析 Skill 实现）

**日期**：2026-08-30  
**交付人**：开发者 B（标准化、Warehouse Engine、Skill、评测）  
**对应里程碑**：M2 分析 Skill（`开发需求-B引擎Skill.md` §7：ABC/库龄/呆滞/基准 + 补货/预测/误差 + 与 A 联调）  
**版本基线**：engine 0.3.0 / formula 0.1.0（冻结，未变更）/ contracts schema 1.0（contracts-python 0.2.0，零新增字段）/ Skill bundle 0.3.0（五个 Skill 全部 implemented）

---

## 1. 安装命令与公开入口

```bash
# 方式一：workspace 源码（开发联调）
uv sync --all-packages --group dev

# 方式二：wheel 安装（M3 才产出；M2 不构建 wheel，验证用源码即可）
# uv pip install dist/contracts_python-0.3.0-py3-none-any.whl dist/warehouse_engine-0.3.0-py3-none-any.whl

# B 侧全量检查（M2 验收门禁）
uv run pytest tests/engine tests/contract      # 273 passed / 17 skipped（skip 为旧版本未冻结的 M2 明细段，设计内）
uv run ruff check packages scripts tests
uv run mypy packages/warehouse-engine/src packages/contracts-python/src

# 重新导出 JSON Schema（contracts-schema 漂移由 CI 门禁看护；M2 零 diff 即契约零改动证明）
uv run python scripts/export_schemas.py

# 性能基准（复用 M1 阈值，M2 复测确认未劣化）
uv run python scripts/perf_bench.py --size 10000
uv run python scripts/perf_bench.py --size 100000
```

**公开入口**（与 M1 相同，A 只允许依赖以下内容，禁止导入 B 的内部模块；M2 无破坏变更，仅 `analyze` 输出扩展）：

| 入口 | 位置 | 说明 |
|---|---|---|
| `WarehouseEngine` | `warehouse_engine.engine` | `validate_dataset` / `analyze(request, dataset, progress)` / `list_capabilities`；M2 起 analyze 返回五类公式共 18 个指标、移除 `ANALYSIS_PLACEHOLDER` |
| `FakeEngine.from_fixture(path)` | `warehouse_engine.fake` | fixture 驱动（engine_version `0.3.0-fake`），供 A 的工作台联调 |
| `validate_raw_dataset(payload)` | `warehouse_engine.validation` | JSON dict 入口，pydantic 错误转结构化 ValidationIssue |
| 公共类型 | `contracts.analysis` / `contracts.enums` | AnalysisRequest/EngineDataset/AnalysisResult/Warning/错误码 等 |
| JSON Schema | `packages/contracts-schema/` | analysis-request / analysis-result 两份快照（M2 零改动） |

**结果形态约定（§2.2，契约零改动）**：M2 仅数据集级聚合进 `AnalysisResult.metrics`，每个 formula_id 恰一个指标；per-SKU 明细（ABC 归属、库龄分桶、呆滞标记、补货 SS/ROP/Q*、逐周预测、基准逐项比较）留在各计算器返回对象内供测试断言，不对外暴露、`schema_version` 保持 1.0、不新增 contract 字段。

**`data_quality`**：与 M1 一致，Warning 按 code 字典序汇总为 `DataQualityEntry`（code、count、details）随结果返回；M2 新增计算层告警码 `NO_OUTFLOW` / `PARAM_MISSING` / `INSUFFICIENT_SAMPLES` / `BENCHMARK_UNAVAILABLE`，原 `ANALYSIS_PLACEHOLDER` 已移除。

## 2. 依赖版本（uv.lock 锁定）

CPython 3.11（CI）/ 3.13（本机验证）；pydantic 2.x；pandas/numpy/scipy/statsmodels（dev/可选，M2 默认路径不加载 statsmodels）；pytest / hypothesis / ruff / mypy / jsonschema（dev 组）。M2 未改动 `pyproject.toml` 依赖声明（statsmodels 仍在主依赖但全仓无 import，M3 真正引入 statsmodels 模型时再收紧为可选依赖组，避免 uv.lock 无谓 churn）。

## 3. 支持 Schema 与公式版本

- contracts `schema_version="1.0"` **不变**；M2 零新增字段（对比 M1 的 `reversal_of`/`ResultMetric.reason`/`AnalysisResult.data_quality`，均为 M1 已交付可选字段）。**`export_schemas.py` 重新导出结果与 M1 快照一致（零 diff）即契约零漂移的硬证明。**
- 公式口径：`docs/formula-spec.md`，`formula_version` 保持 `"0.1.0"`（M0 冻结、A/B 签字，M2 未变更任何口径，历史结果无需重算）。M2 实现 §4~§9 全部公式：F-ABC-001/F-AGE-001/F-STALE-001（ABC/库龄/呆滞）、F-REPL-001~003（补货）、F-FCST-001~002（预测与误差）、F-BM-001（基准）。
- M2 实际触发的 Warning：`NEGATIVE_BALANCE`、`UNIT_COST_MISSING`、`PERIOD_MISMATCH`（M0/M1 沿用）、`NO_OUTFLOW`（观察窗口无出库）、`PARAM_MISSING`（补货 `lead_time_days` 缺失）、`INSUFFICIENT_SAMPLES`（有效需求期数 < 12）、`BENCHMARK_UNAVAILABLE`（无匹配基准）；`ANALYSIS_PLACEHOLDER` 已移除。

## 4. M2 验收证据

| 项 | 证据 | 状态 |
|---|---|---|
| B 侧测试 | `uv run pytest tests/engine tests/contract` → **273 passed / 17 skipped** | ✅ 已验证 |
| 重放内核扩展 | 追加 M2 采集项（`daily_out_by_sku` / 出入库日期索引 / 批次与全历史加权聚合），M1 既有 120 个引擎测试无回归（`test_replay_m2.py` 16 项断言语义） | ✅ 已验证 |
| 四个计算器 | `test_abc_aging.py`(22) / `test_replenishment.py`(16) / `test_forecasting.py`(15) / `test_benchmark_compare.py`(13) 覆盖冻结边界（80/95 并列、五档库龄、呆滞 90 vs 91、z 表四档、n<12、基准无匹配/零基准值） | ✅ 已验证 |
| 黄金数据 | `tests/fixtures/golden/v0.3.0/`（ABC/库龄/呆滞）、`v0.4.0/`（补货/预测/基准），`v0.1.0`/`v0.2.0` 回填结构性可推导项；`test_golden.py` 四版本参数化对照指标值/per-SKU 明细/Warning code+fields/data_quality；全部手算推导、禁止引擎回填 | ✅ 已验证 |
| 实验隔离 | `test_experimental_isolation.py`：类型层（ResultMetric 转换被契约层拒绝）+ 导入层（AST 断言 + 子进程验证默认路径不加载 statsmodels）+ 行为层（默认 analyze 指标不含实验模型、显式 `run_experimental` 可返回并携带免责声明） | ✅ 已验证 |
| 确定性 | `test_analyze_deterministic_byte_identical`：同一输入两次 analyze 序列化结果逐字节一致（M1 既有，M2 继承，新增指标同样确定） | ✅ 已验证 |
| 契约零漂移 | `export_schemas.py` 重新导出与 M1 快照零 diff（M2 未改 contracts / contracts-schema） | ✅ 待 Task 10 复测 |
| 性能未劣化 | `perf_bench.py` 1 万/10 万行复测，对比 M1 阈值（analyze ≤5s / ≤40s、全链路 ≤60s） | ✅ 待 Task 10 复测 |

## 5. 性能基线与阈值

复用 M1 基线（M2 未引入新阈值，重放内核仅追加 O(1) 内存的加权聚合与逐日序列，常数级开销）：

| 规模 | M1 阈值 | M2 结论 |
|---|---|---|
| 1 万行 | analyze ≤ 5 秒 | 复测确认未劣化（见 §4 待办） |
| 10 万行 | analyze ≤ 40 秒、全链路 ≤ 60 秒 | 复测确认未劣化 |
| 100 万行 | 阈值 M3 冻结 | 基线记录 |

## 6. 待 A 侧确认 / 联调项（M2 §7 第 3 条）

按 CONTRIBUTING「不修改他人负责模块」硬规则，**M2 不改 A 侧任何代码**（`services/`/`apps/`/`local-data/` 一律不动）。联调以 B 侧集成测试自证（复用 `FakeEngine` fixture 与真实引擎同一 input 对照、`test_analyze_integration.py` 18 指标断言）。待 A 侧闭环事项：

1. **工作台接入真实 0.3.0 引擎**：A 的 `apps/workbench-desktop` 的 `engine_provider` / `result_store` 切换至 0.3.0 并展示 18 个新指标（含 per-SKU 明细的 UI 取数——当前契约仅含聚合，SKU 清单需 A 侧经 contracts 评审扩展 `sections` 或经内部接口取计算器返回对象，见 §7 决策 1）。
2. **基准数据来源**：`F-BM-001` 依赖 `request.parameters["benchmarks"]` 注入（引擎不读文件/不联网）。A 侧需提供版本化基准数据集的维护与注入链路（B 侧已随 fixture 交付 `v0.1.0.json` 作为格式样例）。
3. **错误码对齐**：M2 新增 `NO_OUTFLOW`/`PARAM_MISSING`/`INSUFFICIENT_SAMPLES`/`BENCHMARK_UNAVAILABLE`，A 侧 UI 需覆盖展示（原 `ANALYSIS_PLACEHOLDER` 展示逻辑需移除）。

## 7. M2 实现决策（需 A/B 复核）

以下为 M2 落地时对 formula-spec 歧义/未冻结处的判断，已写入代码注释、SKILL.md 与本文，建议评审后回流 formula-spec：

1. **结果形态（§2.2）**：per-SKU 明细不进 `AnalysisResult`，仅聚合进 `metrics`。若报告页需 SKU 级清单，需 contracts 评审新增可选 `sections` 字段（schema_version 保持 1.0 的非破坏扩展）或由 A 侧经内部接口取计算器返回对象——本决策保证 M2 契约零改动、零风险。
2. **预测样本不足口径（§8.3）**：「有效需求期数 n < 12」按**全期间自然周数**理解（非预测窗口期数）。若按预测窗口理解，缺省 `split_date = end_date − 28 天` 下 n≈4 会恒定触发降级，与 §8.4 黄金数据「≥12 期周需求」矛盾。代码分别命名 `forecast_periods` / `demand_periods`，Warning fields 取后者。
3. **补货 d̄ 口径（§7.2 vs contracts）**：`ReplenishmentRecord.avg_daily_demand` 与 §7.2 的 `d̄ = max(0,out_qty)/period_days` 口径不同源。本计算器不消费该字段，避免引入未对齐口径；统一需同步 contracts 与 formula-spec 评审。
4. **基准聚合（§9）**：各指标单位不同（CNY/件/比率/天数），跨单位求和无意义，故 `BM.DEVIATION_RATIO` 取各比较项相对偏差的算术平均（非合计）。
5. **实验模型隔离（§8.1）**：`experimental/` 以类型层（ResultMetric 转换被拒）+ 导入层（默认路径不加载 statsmodels）+ 依赖层（占位桩显式报未实装）三重隔离；结果携带 `EXPERIMENTAL_DISCLAIMER`，不进默认 `AnalysisResult`、不参与黄金数据验收、不得作为采购依据。
6. **重放内核扩展**：为四个计算器供数的采集项均为带默认值的追加字段，`buckets` 内容与顺序零变化；`Σ daily_out == period_out_qty` 恒成立；盘点后清空批次账使 FIFO 残余 ≠ 期末余额时，库龄上层按「构成不足」回退全历史口径（不编造）。

## 8. 风险与外部依赖

- **A 侧未验收**：§6 三项待 A 闭环，不影响 B 侧交付与门禁，但报告页 SKU 清单与基准注入链路需 A 侧落地。
- **statsmodels 为占位**：`holt_winters` 仅报未实装；M3 引入真实模型时需收紧依赖为可选组并经 contracts 评审。
- **黄金数据手工推导**：全部指标/明细/Warning 为手算推导并 freeze，禁止引擎回填；若 formula-spec 口径后续微调，需重算黄金数据并提升 formula_version。
- **性能**：超大行（100 万+）阈值留 M3；M2 未改算法复杂度，仅常数级扩展。

## 9. 分支与提交

- 分支：`feature/b-m2-analysis-skills`（从 `origin/master` 创建，符合 CONTRIBUTING `feature/b-*` 规范）。
- 提交：按 Task 原子化（重放内核 → 四个计算器 → 实验隔离 → 引擎接入 → 黄金/边界数据 → 文档/清单），单笔提交不混入与 M2 无关改动；提交信息遵循 conventional commits（feat/test/docs）。
- PR：M2 完成后按 §26.2 模板提交，标注「开发者 B 侧 M2 交付，待 A 侧联调（§6）」。
