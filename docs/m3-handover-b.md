# 开发者 B：M3 交接说明（稳定性与发布）

**日期**：2026-09-07  
**交付人**：开发者 B（标准化、Warehouse Engine、Skill、评测）  
**对应里程碑**：M3 稳定性与发布（`开发需求-B引擎Skill.md` §7 M3、§8.3 完成定义、§9 交接包）  
**版本基线**：engine 0.3.0（wheel 已发布）/ formula 0.1.0（冻结，未变更）/ contracts schema 1.0（contracts-python 0.3.0，零字段变更）/ Skill bundle 0.3.0（五个 Skill 全部 implemented）  
**发布形态**：`dist/` 4 构建产物 + `SHA256SUMS` + SBOM + 依赖清单（dist 不入库，随 release 附件交付；本地构建命令见 §1）

---

## 1. 安装命令与公开入口

```bash
# 方式一：workspace 源码（开发联调）
uv sync --all-packages --group dev

# 方式二：wheel 安装（M3 正式发布形态；依赖仅 pydantic 2.x + numpy/pandas/scipy/statsmodels）
uv pip install dist/contracts_python-0.3.0-py3-none-any.whl dist/warehouse_engine-0.3.0-py3-none-any.whl

# B 侧检查（M3 验收门禁）
uv run pytest tests/engine tests/contract        # 284 passed / 17 skipped
uv run pytest                                    # workspace 全量 572 passed / 19 skipped
uv run ruff check packages scripts tests
uv run mypy packages/warehouse-engine/src packages/contracts-python/src

# 契约零漂移复证（CI 门禁同款）
uv run python scripts/export_schemas.py          # 与 packages/contracts-schema/ 零 diff

# 独立环境 wheel 冒烟（不依赖 workspace 源码）
uv run python scripts/wheel_smoke.py             # 全新 venv 仅装双 wheel + 黄金 v0.1.0 完整断言

# staging 三项验证（脱敏数据 + 兼容 + 回滚 + 可追溯）
uv run python scripts/staging_verify.py

# 本地重建发布产物
uv build --package contracts-python --out-dir dist
uv build --package warehouse-engine --out-dir dist
uv run python scripts/export_sbom.py             # 重建 SBOM + 依赖清单
```

**公开入口**（与 M1/M2 相同，A 只允许依赖以下内容；M3 零变更）：

| 入口 | 位置 | 说明 |
|---|---|---|
| `WarehouseEngine` | `warehouse_engine.engine` | `validate_dataset` / `analyze(request, dataset, progress)` / `list_capabilities`；五类公式 18 指标 |
| `FakeEngine.from_fixture(path)` | `warehouse_engine.fake` | fixture 驱动（engine_version `0.3.0-fake`），供 A 的工作台联调 |
| `validate_raw_dataset(payload)` | `warehouse_engine.validation` | JSON dict 入口，pydantic 错误转结构化 ValidationIssue |
| 公共类型 | `contracts.analysis` / `contracts.enums` | AnalysisRequest/EngineDataset/AnalysisResult/Warning/错误码 等 |
| JSON Schema | `packages/contracts-schema/` | analysis-request / analysis-result 两份快照（M3 复导出零 diff） |

## 2. 依赖版本（uv.lock 锁定，`dist/requirements-engine-0.3.0.txt`）

Python 3.11.15（CI 与 staging venv 均为 3.11）；pydantic 2.13.5 / pydantic_core 2.46.5；numpy 2.4.6；pandas 2.3.3；scipy 1.17.1；statsmodels 0.14.6（默认分析路径不加载，仅 `experimental/` 引用）；其余传递依赖见清单。SBOM（`dist/sbom-engine-0.3.0.json`，CycloneDX 1.5）覆盖两个发布包 + 上述依赖闭包。

## 3. 支持 Schema 与公式版本

- contracts `schema_version="1.0"` **不变**；M3 零字段变更。**零漂移硬证明**：2026-09-07 复跑 `scripts/export_schemas.py`，导出结果与 `packages/contracts-schema/` 快照零 diff（CI schema 漂移门禁同款检查）。
- 公式口径：`docs/formula-spec.md`，`formula_version` 保持 `"0.1.0"`（M0 冻结、A/B 签字，M2/M3 均未变更，历史结果无需重算；staging 双版本对比 9 个共享指标值一致即口径未变的运行时证明）。
- M3 触发的 Warning 码与 M2 相同，无新增：`NEGATIVE_BALANCE`、`UNIT_COST_MISSING`、`PERIOD_MISMATCH`、`NO_OUTFLOW`、`PARAM_MISSING`、`INSUFFICIENT_SAMPLES`、`BENCHMARK_UNAVAILABLE`。

## 4. M3 验收证据（对照需求 §8.3 / spec DoD）

| # | DoD 项 | 证据 | 状态 |
|---|---|---|---|
| 1 | 全量测试通过（新增确定性/版本一致性/SBOM 测试，既有零删除零跳过） | workspace 572 passed / 19 skipped；B 侧 284 passed / 17 skipped（M2 基线 273/17 + 新增 11 项） | ✅ |
| 2 | contracts 零漂移 | `export_schemas.py` 复导出零 diff（2026-09-07，本分支内容与 master 快照一致） | ✅ |
| 3 | dist 4 产物 + SHA256SUMS + SBOM + 依赖清单；独立 venv wheel 冒烟 | `dist/`（SHA-256 复算一致）；`scripts/wheel_smoke.py` 全新 venv 冒烟通过（18 指标 / digest `dd3200c337e1e5b3…`） | ✅ |
| 4 | perf_bench 1 万/10 万 ≥3 轮达标 | 见 §5（1 万 ≤0.59s、10 万 ≤8.31s、全链路 ≤12.4s，均远低于阈值） | ✅ |
| 5 | staging 三项验证留档 | `docs/m3-staging-log-b.md`（R3.1/R3.2/R3.3 全 PASS，结果哈希归档） | ✅ |
| 6 | CHANGELOG 定稿 + 交接文档 + 兼容矩阵 | `CHANGELOG.md` 0.3.0 条目、本文、`docs/compatibility-matrix.md` | ✅ |
| 7 | PR 合并 + CI 绿 | PR 见 `feature/b-m3-stability-release`（本 PR），CI 含 schema 漂移门禁 | 🔄 评审中 |

## 5. 性能复测数据（M3 发布门槛，2026-09-06，engine 0.3.0，seed 20260829）

| 规模 | 阈值（M1 冻结） | 3 轮实测（analyze） | 3 轮实测（全链路 build+validate+analyze+digest） | 结论 |
|---|---|---|---|---|
| 1 万行 | analyze ≤ 5 秒 | 0.5195 / 0.592 / 0.4537 秒 | ≈1.0–1.3 秒 | ✅ 余量约 10 倍 |
| 10 万行 | analyze ≤ 40 秒、全链路 ≤ 60 秒 | 8.2695 / 8.1573 / 8.3003 秒 | 12.35 / 11.92 / 11.97 秒 | ✅ 余量约 5 倍 |
| 100 万行 | 无阈值（选跑记录） | 1155.11 秒（1 轮） | ≈1211.8 秒（26.70 + 6.33 + 1155.11 + 23.65） | 📝 仅记录 |

**100 万行说明**：M1 基线（engine 0.2.0，仅 KPI/COGS）为 171.25 秒；0.3.0 劣化约 6.7 倍，来源为 M2 五类公式的 per-SKU 计算（ABC/库龄/呆滞/补货/预测）与重放内核扩展（逐日出库序列、批次余额、加权聚合）。1 万/10 万档位仍以约 5 倍余量满足 M1 阈值；100 万行阈值冻结建议留 M3 后规划（候选方向：per-SKU 聚合向量化、逐日序列惰性计算），不在本里程碑范围。

## 6. staging 结论摘要（详情 `docs/m3-staging-log-b.md`）

- **R3.1 版本兼容**：0.3.0 双 wheel 装入独立 venv → manifest 5 Skill 全命中 → 黄金 v0.1.0 冒烟通过 → staging 数据集 18 指标结果归档。
- **R3.2 回滚演练**：降级 0.2.0 → manifest 仅命中 kpi（4 个 0.3.0-only Skill 被拒）→ contracts 0.2.0 Schema 导出与仓库快照零 diff → 黄金 v0.1.0/v0.2.0 通过 → 升回 0.3.0 后 staging 结果与首次运行逐字节一致（SHA-256 相同）。
- **R3.3 结果可追溯**：同一数据集双版本 dataset_digest 一致（`0b2e06e07781cc34…`），9 个共享 KPI/COGS 指标值完全一致；差异仅 0.3.0 新增 9 指标与真实告警取代 `ANALYSIS_PLACEHOLDER`，逐条解释在留档文档 §5。
- 回滚版本：0.2.0（M1 wheel，兼容矩阵见 §10）。

## 7. §9 交接包清单逐项索引

| 需求 §9 项 | 仓库路径 | 说明 |
|---|---|---|
| `dist/warehouse_engine-<version>-py3-none-any.whl` | `dist/warehouse_engine-0.3.0-py3-none-any.whl`（+ sdist） | 另含 `contracts_python-0.3.0` wheel + sdist，共 4 产物 |
| `dist/SHA256SUMS` | `dist/SHA256SUMS` | 覆盖 4 产物，本地复算一致 |
| `contracts/analysis-request.schema.json` | `packages/contracts-schema/analysis-request.schema.json` | M3 复导出零 diff |
| `contracts/analysis-result.schema.json` | `packages/contracts-schema/analysis-result.schema.json` | 同上 |
| `fixtures/golden/<dataset-version>/input.json` | `tests/fixtures/golden/v0.1.0`–`v0.4.0/input.json` | 四版本成对 |
| `fixtures/golden/<dataset-version>/expected.json` | `tests/fixtures/golden/v0.1.0`–`v0.4.0/expected.json` | 全部手算冻结，禁止引擎回填 |
| `skills/*/SKILL.md` | `skills/{kpi,abc-aging,replenishment,forecasting,benchmark}/SKILL.md` | 五个 Skill 全部 implemented |
| `CHANGELOG.md` | `CHANGELOG.md` | 0.3.0 条目已定稿（含 M3 段落） |
| `compatibility-matrix.md` | `docs/compatibility-matrix.md` | M3 更新（Engine wheel 发布状态、contracts-python 0.3.0） |
| `benchmark/<dataset-version>.json` | `tests/fixtures/benchmarks/v0.1.0.json` | 版本化基准 fixture（经 `request.parameters["benchmarks"]` 注入） |
| （发布附加）SBOM | `dist/sbom-engine-0.3.0.json` | CycloneDX 1.5，两发布包 + 依赖闭包 |
| （发布附加）依赖清单 | `dist/requirements-engine-0.3.0.txt` | Python 3.11.15 锁定版本摘要 |
| （发布附加）staging 留档 | `docs/m3-staging-log-b.md` | 环境 / 指纹 / 命令 / 哈希 / 结论 |

## 8. 待 A 侧确认 / 联调项

1. **端到端联调（staging R3.1 的 A 侧半边）**：workbench 1.0.0 调用 engine 0.3.0 完成一次真实分析链路（B 侧已以独立 venv wheel + manifest 校验 + 黄金冒烟自证引擎半边；A 侧操作 workbench 的结论请回填 `docs/m3-staging-log-b.md` §8）。**待 A 回填**。
2. **打包引用**：A 的安装包链路（`scripts/build_release.py` / Inno Setup）如需捆绑 engine wheel，请以 `dist/SHA256SUMS` 校验后引用（4 产物哈希见清单）。
3. **M2 遗留三项**（`docs/m2-handover-b.md` §6）状态更新：工作台接入真实 0.3.0 引擎、基准数据注入链路、新告警码 UI 展示——随 A 侧 M3 后续版本确认。

## 9. 已知限制

- 100 万行 analyze 约 19.25 分钟（M1 基线 171 秒，劣化 6.7 倍，原因见 §5）；未设新阈值，优化方向已登记。
- per-SKU 明细（ABC 归属、库龄分桶、补货建议、逐周预测、基准逐项）仍在计算器返回对象内，不进 `AnalysisResult`（M2 §7 决策 1，契约零改动）；报告页需 SKU 清单时走 contracts 评审扩展。
- statsmodels 仍在主依赖声明（默认路径不加载）；收紧为可选依赖组的动作留后续（避免 M3 无谓 uv.lock churn）。

## 10. 回滚版本

| 组件 | 当前 | 回滚目标 | 方式 |
|---|---|---|---|
| warehouse-engine | 0.3.0 | 0.2.0 | `uv pip install warehouse_engine-0.2.0-py3-none-any.whl contracts_python-0.2.0-py3-none-any.whl`（M1 wheel） |
| contracts-python | 0.3.0 | 0.2.0 | 随 engine 同降（Schema 两版本零 diff，staging R3.2 已演练） |

回滚后行为（staging R3.2 实测）：仅 kpi Skill 可用（manifest 拒绝四个 0.3.0-only Skill）；KPI/COGS 9 指标结果与 0.3.0 完全一致（formula 0.1.0 未变）；四类公式回退 `ANALYSIS_PLACEHOLDER` 告警。
