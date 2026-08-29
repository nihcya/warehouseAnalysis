# 开发者 B：M1 交接说明（G3 前置验收材料）

**日期**：2026-08-29  
**交付人**：开发者 B（标准化、Warehouse Engine、Skill、评测）  
**对应里程碑**：M1 KPI 引擎（`开发规划与协作需求文档.md` V2.0 §5.2 B2/§6 B2、§38.4 G3；`开发需求-B引擎Skill.md`）  
**版本基线**：engine 0.2.0 / formula 0.1.0（冻结，未变更）/ contracts schema 1.0（contracts-python 0.2.0）/ Skill bundle 0.2.0（kpi implemented，其余四个 skeleton）

---

## 1. 安装命令与公开入口

```powershell
# 方式一：workspace 源码（开发联调）
uv sync --all-packages --group dev

# 方式二：wheel 安装（对外分发；两 wheel 一起安装，engine 依赖 contracts）
uv pip install dist/contracts_python-0.2.0-py3-none-any.whl dist/warehouse_engine-0.2.0-py3-none-any.whl

# B 侧全量检查
uv run pytest tests/engine tests/contract      # 147 passed
uv run ruff check packages scripts tests
uv run mypy packages/warehouse-engine/src packages/contracts-python/src

# 重新导出 JSON Schema（contracts-schema 漂移由 CI 门禁看护）
uv run python scripts/export_schemas.py

# 性能基准（M1 起 1 万/10 万行有阈值，见 §5）
uv run python scripts/perf_bench.py --size 10000
```

wheel 产物（`dist/` 本地构建，哈希见 `dist/SHA256SUMS`）：

| 产物 | SHA256 |
|---|---|
| `contracts_python-0.2.0-py3-none-any.whl` | `f38e09f0478838d28ffe08db257ac2cf9034a20ed5b835df1bf05882357c26ba` |
| `warehouse_engine-0.2.0-py3-none-any.whl` | `0050fddbf48e7706f1977f396c89ca8495ca0942fdc537bde45a5f4c52fb8abb` |
| `contracts_python-0.2.0.tar.gz` | `9507fd2fa17cceb6eaa609876cef61cddf3d162aead4cda5cf9ffb2546ba53ca` |
| `warehouse_engine-0.2.0.tar.gz` | `ef543e45cddcf0aec6d65831bc952f12acf648e4e39769b2656f20ce2277fd24` |

公开入口（与 M0 相同，A 只允许依赖以下内容，禁止导入 B 的内部模块；M1 无破坏变更）：

| 入口 | 位置 | 说明 |
|---|---|---|
| `WarehouseEngine` | `warehouse_engine.engine` | `validate_dataset` / `analyze(request, dataset, progress)` / `list_capabilities`；M1 起 analyze 返回真实 KPI/COGS 结果 |
| `FakeEngine.from_fixture(path)` | `warehouse_engine.fake` | fixture 驱动（engine_version `0.2.0-fake`），供 A 的工作台联调 |
| `validate_raw_dataset(payload)` | `warehouse_engine.validation` | JSON dict 入口，pydantic 错误转结构化 ValidationIssue |
| 公共类型 | `contracts.analysis` / `contracts.enums` | AnalysisRequest/EngineDataset/AnalysisResult/Warning/错误码 等；0.2.0 新增可选字段见 §3 |
| JSON Schema | `packages/contracts-schema/` | analysis-request / analysis-result 两份快照 |

**`data_quality` 新增说明**：`WarehouseEngine.analyze` 结果的 `AnalysisResult.data_quality`（可选字段）为数据质量报告——Warning 按 code 字典序汇总为 `DataQualityEntry`（code、count、details 明细）随结果一次性返回，UI 无需自行分组统计；FakeEngine 结果与 M0 旧结果该字段为 `None`（表示未生成），消费方需判空处理。

## 2. 依赖版本（uv.lock 锁定）

CPython 3.11.15；pydantic 2.13.5；pandas 2.3.3；numpy 2.4.6；scipy 1.17.1；statsmodels 0.14.6；pytest / hypothesis / ruff / mypy / jsonschema（dev 组）。wheel 独立安装时运行时依赖由 pip/uv 按各包 `pyproject.toml` 解析（engine：contracts-python、pandas≥2.2、numpy≥1.26、scipy≥1.11、statsmodels≥0.14；contracts：pydantic≥2）。

## 3. 支持 Schema 与公式版本

- contracts `schema_version="1.0"` **不变**；contracts-python 包版本 0.1.0 → 0.2.0，新增三个**可选字段（非破坏，旧消费方可安全忽略）**：
  - `MovementRecord.reversal_of`：被冲销事件的 `event_id`（仅 REVERSAL 使用；引用缺失或成环在校验层以 `DATA_VALIDATION_FAILED` 阻断）；
  - `ResultMetric.reason`：指标值为 `"null"` 或降级时的原因标注（如 `empty_dataset`、`no_outflow`、`avg_inventory_value_nonpositive`）；
  - `AnalysisResult.data_quality`：数据质量报告（见 §1）。
  金额/数量 Decimal（数量 scale≤3、金额 scale≤2、HALF_UP、序列化为字符串）、日期 `YYYY-MM-DD`、时间 UTC ISO 8601 等既有约定不变。
- 公式口径：`docs/formula-spec.md`，`formula_version` 保持 `"0.1.0"`（M0 冻结、A/B 签字，M1 未变更任何口径，历史结果无需重算）。M1 实现 §3 全部九个公式：F-KPI-001~008 与 F-COGS-001（COGS 为移动加权平均，批次 FIFO 列为后续 formula_version）；§4~§9（ABC/库龄/呆滞/补货/预测/基准）口径已冻结、实现属 M2。
- M1 实际触发的 Warning：`NEGATIVE_BALANCE`（重放负余额，fields 含 sku/仓库/首个负余额日期与数值）、`UNIT_COST_MISSING`（金额口径缺 unit_cost，fields: sku_id，含快照回退）、`PERIOD_MISMATCH`（move_date 超出期间）、`ANALYSIS_PLACEHOLDER`（四类占位标注）；M1 新增阻断校验：冲销引用缺失/成环、盘点实盘数量非正（`DATA_VALIDATION_FAILED`）。

## 4. M1 验收证据（G3 前置）

| 项 | 证据 | 状态 |
|---|---|---|
| B 侧测试 147 项全绿 | `uv run pytest tests/engine tests/contract` → **147 passed**（engine 120 + contract 27），覆盖 replay 内核、inventory_kpi、校验规则、黄金数据、FakeEngine、analyze 集成与库存守恒/确定性属性测试 | ✅ 已验证 |
| 黄金数据指标层 | `tests/fixtures/golden/v0.1.0/`（M0 校验层扩展至指标数值层）+ `tests/fixtures/golden/v0.2.0/`（M1 新增边界）；`tests/engine/test_golden.py` 双版本参数化对照指标值、per-SKU 明细、Warning code+fields 与 data_quality | ✅ 已验证 |
| 确定性 | `tests/engine/test_analyze_deterministic_byte_identical`：同一输入两次 analyze 序列化结果逐字节一致；`input_summary.dataset_digest`（数据集规范 JSON 的 SHA-256）供重复运行一致性核对 | ✅ 已验证 |
| 独立环境 wheel 验证 | 干净 venv 安装 `dist/` 两 wheel → 以黄金数据 v0.1.0 输入运行 `WarehouseEngine.analyze`：engine_version 0.2.0、9 个指标（F-KPI-001~008 + F-COGS-001）、data_quality 报告 3 条、两次运行序列化字节一致 | ✅ 已验证 |

注：全仓 `uv run pytest`（含 A 侧 `services/`）另有 1 项 A 侧 `test_openapi_sync` 换行符漂移失败与 1 项跳过，属 A 侧文件，与 B 侧交付无关（B 侧范围全绿）。

## 5. 性能基线与阈值

环境：Windows 10 / i7-2600S @2.80GHz 4核8线程 / 16GB / CPython 3.11.15 / pandas 2.3.3 / numpy 2.4.6 / pydantic 2.13.5，seed=20260829（`scripts/perf_bench.py`）。

| 规模 | analyze 实测（两次运行） | 全链路（build+validate+analyze+digest） | M1 阈值 | 结论 |
|---|---|---|---|---|
| 1 万行 | 1.49 / 1.80 秒 | — | analyze ≤ 5 秒 | ✅ 达标 |
| 10 万行 | 15.80 / 12.38 秒 | 约 36 秒 | analyze ≤ 40 秒、全链路 ≤ 60 秒 | ✅ 达标 |
| 100 万行 | 171.25 秒（仅 1 次实测） | — | 阈值 M3 冻结 | 基线记录，暂不设阈值 |

## 6. 已知限制

- `lot_id` 不参与 M1 KPI/COGS 计算（formula-spec §3 组级输入声明 lot_id 在 M0/M1 不参与；批次 FIFO COGS 属后续 formula_version）。
- abc-aging / replenishment / forecasting / benchmark 四类计算器维持 M0 占位（`ANALYSIS_PLACEHOLDER` 非阻断 Warning），实现属 M2；对应四个 Skill 仍为 skeleton。
- 100 万行性能阈值未定（M3 按基线数据冻结），当前仅单次实测样本。
- 聚合粒度（`aggregation_level`）、周转年化等参数化切换未开放：M1 按冻结默认口径计算（指标为数据集级聚合 + SKU 级明细，见各 SKILL.md 参数表）。
- A 侧全仓 openapi 快照换行符漂移 1 项测试失败（见 §4 注），需 A 侧重导出 `scripts/export_openapi.py` 后提交修复。

## 7. 回滚版本

- **M0**：无对外发布物（workspace 源码交付），回滚 = 源码 revert（git revert 到本批合并前的 `main`）。
- **M1 起**：engine 与 contracts 以版本化 wheel 发布（`dist/` + `SHA256SUMS`），回滚 = 卸载当前版本 wheel、安装上一版本 wheel（如 0.3.0 → 0.2.0），无需源码操作；M1 为首个 wheel 版本，暂无更早 wheel 可回退（等价路径为回退到 M0 源码方式）。`formula_version` 未变更（0.1.0 冻结），历史 `AnalysisResult` 无需重算。

## 8. 与 A 的待办协调点

1. **wheel 消费反馈**：A 在工作台实际环境安装 `dist/` 两 wheel（一并安装，先 contracts 后 engine 亦可），验证依赖解析、import、FakeEngine → WarehouseEngine 切换与 `data_quality` 展示，反馈安装与兼容问题。
2. **UNIT_COST_MISSING 快照数据质量验证**：A 以本地库 `stock_snapshot.inventory_value` 真实数据核对 F-KPI-005 回退口径（formula-spec §3.5：unit_cost 缺失时取该 SKU 最近一条快照的 inventory_value 替代），确认回退值可用性与 Warning 的 UI 呈现方式。
3. **本周交接反馈环**：本交接文档评审 + A 侧联调证据（M0 遗留 Task 7.1 剩余项：工作台接入 FakeEngine/真实引擎并展示结果）一并纳入 G3 前置验收；问题本周内闭环后再进入 M2（abc-aging/replenishment/forecasting/benchmark 实现）。
