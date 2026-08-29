# 开发者 B：M0 交接说明（G2 前置验收材料）

**日期**：2026-08-29  
**交付人**：开发者 B（标准化、Warehouse Engine、Skill、评测）  
**对应里程碑**：M0 契约与基线（`开发规划与协作需求文档.md` V2.0 §22.2、`开发需求-B引擎Skill.md` §7）  
**版本基线**：engine 0.1.0 / formula 0.1.0-draft / contracts 1.0 / Skill bundle 0.1.0

---

## 1. 安装命令与公开入口

```powershell
# 环境安装（uv 自动解析 Python 3.11）
uv sync --all-packages --group dev

# 全量检查
uv run pytest                      # 47 passed
uv run ruff check packages scripts tests
uv run mypy packages/warehouse-engine/src packages/contracts-python/src

# 重新导出 JSON Schema（contracts-schema 漂移由 CI 门禁看护）
uv run python scripts/export_schemas.py

# 性能基准占位（M0 不设阈值）
uv run python scripts/perf_bench.py --size 10000
```

公开入口（A 只允许依赖以下内容，禁止导入 B 的内部模块）：

| 入口 | 位置 | 说明 |
|---|---|---|
| `WarehouseEngine` | `warehouse_engine.engine` | `validate_dataset` / `analyze(request, dataset, progress)` / `list_capabilities` |
| `FakeEngine.from_fixture(path)` | `warehouse_engine.fake` | fixture 驱动，供 A 的工作台在真实引擎完成前联调 |
| `validate_raw_dataset(payload)` | `warehouse_engine.validation` | JSON dict 入口，pydantic 错误转结构化 ValidationIssue |
| 公共类型 | `contracts.analysis` / `contracts.enums` | AnalysisRequest/EngineDataset/AnalysisResult/Warning/错误码 等 |
| JSON Schema | `packages/contracts-schema/` | analysis-request / analysis-result 两份快照 |

## 2. 依赖版本（uv.lock 锁定）

CPython 3.11.15；pydantic 2.13.5；pandas 2.3.3；numpy 2.4.6；scipy 1.17.1；statsmodels 0.14.6；pytest / hypothesis / ruff / mypy / jsonschema（dev 组）。

## 3. 支持 Schema 与公式版本

- contracts `schema_version="1.0"`；金额/数量 Decimal（数量 scale≤3、金额 scale≤2，HALF_UP，序列化为字符串）；日期 `YYYY-MM-DD`，时间 UTC ISO 8601。
- 公式口径：`docs/formula-spec.md`（0.1.0-draft，formula_version 0.1.0）。公式 ID 采用 F-* 编号（F-KPI-001~008、F-COGS-001、F-ABC-001、F-AGE-001、F-STALE-001、F-REPL-001~003、F-FCST-001~002、F-BM-001），engine 能力描述与五个 SKILL.md 已对齐该编号。
- Warning 码：ANALYSIS_PLACEHOLDER、PERIOD_MISMATCH、NEGATIVE_BALANCE、UNIT_COST_MISSING（M1 实现）、PARAM_MISSING 等，均含 code/severity/message/fields/blocking 五要素。

## 4. M0 合并门槛验证记录（§22.2）

| 门槛 | 证据 | 状态 |
|---|---|---|
| B 引擎读取同一 fixture 并通过 Schema 校验 | `tests/engine/test_golden.py`、`tests/contract/test_fixture_schema.py`（golden + 7 份 edge fixture 全部过 JSON Schema） | ✅ 已验证 |
| JSON Schema 与 Python 类型由 CI 检查一致 | `tests/contract/test_schema_sync.py` + `engine-ci.yml` 重导出后 `git diff --exit-code` 门禁 | ✅ 已验证（本地哈希比对无漂移） |
| A 的工作台可调用 FakeEngine 并展示结果 | `tests/engine/test_fake_engine.py`（结构完整、run_id/期间覆盖、progress 回调） | ⚠️ B 侧就绪，待 A 工作台实际接入联调（Task 7.1 剩余项） |

## 5. 对照评审报告 P0 自查

### P0-2（冻结口径 + 黄金数据）
- 七类口径（KPI、COGS、ABC、库龄、呆滞、补货、预测/误差）已在 formula-spec.md 形成草案，每口径有公式 ID、版本、边界规则、Warning 码与容差声明。
- 黄金数据已建立：`tests/fixtures/golden/v0.1.0/`（input + expected，M0 冻结校验层；指标层数值随 M1 公式实现后补充）。
- **状态：B 侧完成草案；转"冻结"需 A 走查签字（Task 2.5，待办）**。

### P0-3（contracts 正反例契约测试）
- 正例：最小合法请求、全部 fixture 过 Schema。
- 反例：必填缺失、类型错误、枚举非法、负/零数量、精度违规，均返回 `DATA_VALIDATION_FAILED` 且 `field` 定位到具体字段。
- **状态：✅ 完成**（`tests/contract/`）。`sync-envelope.schema.json` 与 `openapi.json` 为 A 主导的共同文件，B 评审待 A 提交后进行（Task 1.5，待办）。

### 缺口清单（转入 M1，不静默降级）
1. 指标层黄金数值：M1 实现 KPI/清洗后，expected.json 从校验层扩展到数值层（含容差）。
2. COGS 批次 FIFO：M0 冻结移动加权平均，FIFO 列为后续 formula_version。
3. `UNIT_COST_MISSING` 库存价值回退逻辑：口径已冻结，实现随 M1 KPI。
4. 性能阈值：perf_bench 已有 1 万/10 万/100 万入口，阈值 M1/M3 按基线数据设定。
5. wheel 交付：M0 以 workspace 源交付，首个版本化 wheel + SHA256SUMS 随 M1 发布。

## 6. 已知限制

- `analyze` 返回占位结果（`ANALYSIS_PLACEHOLDER`），五个 calculators 为存根——M0 设计如此，公式实现属 M1/M2。
- `validate_dataset(request, dataset)` 签名按主基线 §21.2 Protocol（dataset 独立参数）实现，与 B 需求文档 §4.1（内嵌 request）表述有差异，已在契约评审议题中提出，**以主基线 §21.2 为准**，请 A 确认。
- 预测 statsmodels 模型、实验内容一律位于 experimental 范畴之外才会进入默认结果；M0 无实验代码。

## 7. 回滚版本

M0 无对外发布物；回滚 = git revert 到本批合并前的 `main`。首个需要回滚方案的发布物是 M1 的 engine wheel（届时提供上一版本 wheel + 兼容矩阵）。

## 8. 与 A 的待办协调点

1. **Task 1.5**：共同评审五份公共文件中 B 相关三份（enums.py、analysis.py、两份 JSON Schema）；A 提交 sync-envelope 与 openapi.json 后 B 参与评审。
2. **Task 2.5**：formula-spec.md 走查签字，草案转冻结。
3. **Task 7.1 剩余**：A 工作台以 `FakeEngine.from_fixture` 实际接入并展示结果，记录联调证据。
