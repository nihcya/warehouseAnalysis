# 开发者 B：M0 冻结期引擎基线（contracts + engine 骨架 + FakeEngine + 黄金数据）Spec

## Why

评审报告结论为"有条件通过，准予进入 M0"，其中 G2（开发就绪）当前未通过，要求 contracts、FakeEngine 和 CI 实际提交；同时 P0 清单要求冻结 KPI/COGS/库龄/ABC/补货/预测/误差口径并建立黄金数据。开发者 B 必须在 M0 交付这些基线，A 才能并行开发工作台 UI 而不被阻塞。

## What Changes

- 新增 `packages/contracts-python` 中 B 主导的部分：`enums.py`（move_type、source、错误码、Warning 严重级）与 `analysis.py`（`AnalysisRequest`、`EngineDataset`、`AnalysisResult`、`ValidationReport`、`Warning`）。
- 新增 `packages/contracts-schema` 引擎侧 JSON Schema：`analysis-request.schema.json`、`analysis-result.schema.json`。
- 新增 `packages/warehouse-engine` uv workspace 包骨架：`engine.py`、`contracts.py`、`result.py`、`errors.py`、`validation/`、`calculators/`（五个计算器空壳）。
- 新增 fixture 驱动的 `FakeEngine`（与真实引擎实现同一 `WarehouseEngine` Protocol，供 A 的工作台联调）。
- 新增最小黄金数据与边界数据：`tests/fixtures/golden/<dataset-version>/`、`tests/fixtures/edge/`。
- 新增 `skills/` 五个 Skill 目录的 `SKILL.md` 骨架与 Skill manifest。
- 新增公式口径冻结文档（M0 草案版，含 `formula_version` 标识）：KPI、COGS、库龄、ABC、呆滞、补货、预测与误差口径。
- 新增 B 侧测试与 CI 入口：contracts 正反例测试、Hypothesis 库存守恒属性测试草案、Ruff/mypy、性能脚本占位。
- 新增 `docs/compatibility-matrix.md` 初版与 `CHANGELOG.md`。

**职责边界说明（不在 B 范围）**：Alembic 迁移脚本、SQLAlchemy Model、FastAPI/OpenAPI、PySide6 UI、越权测试均属 A 或共同项；B 仅参与数据库字段评审并提供库存事件守恒规则口径。同步信封 `sync-envelope.schema.json` 与 `openapi.json` 为共同文件，B 参与评审，不由 B 单方面产出。

## Impact

- Affected specs: 无已有 spec（本项目首个 spec）。
- Affected code: 新建 `packages/contracts-python`、`packages/contracts-schema`、`packages/warehouse-engine`、`skills/`、`tests/fixtures/`、`tests/contract/`、`.github/workflows/`、根 `pyproject.toml`（uv workspace）。
- 依赖约束：engine 仅依赖 contracts-python、pandas/numpy/scipy/statsmodels（锁版本）与标准库；禁止依赖 UI、HTTP、ORM、SQLite。

## ADDED Requirements

### Requirement: 标准化数据契约

系统 SHALL 在 `contracts-python` 中定义 `AnalysisRequest`、`EngineDataset`、`AnalysisResult`、`ValidationReport` 与 `Warning` Pydantic 模型，并在 `contracts-schema` 中提供对齐的 JSON Schema；数量与金额使用 Decimal 语义，日期使用 `YYYY-MM-DD`，时间使用 UTC ISO 8601，且每个结果携带 `run_id`、`engine_version`、`formula_version` 与输入摘要。

#### Scenario: 合法请求通过校验
- **WHEN** 引擎收到符合 `analysis-request.schema.json` 的最小合法请求
- **THEN** Schema 校验通过，`validate_dataset` 返回无阻断的 `ValidationReport`

#### Scenario: 非法请求返回结构化错误
- **WHEN** 请求中 `quantity` 缺失或 `move_type` 不在枚举内
- **THEN** 返回 `DATA_VALIDATION_FAILED` 错误码，`details` 定位到字段与原因，不抛出未捕获异常

### Requirement: 统一错误码与 Warning

系统 SHALL 在 `contracts/enums.py` 冻结 B 侧最小错误码集合（含 `DATA_VALIDATION_FAILED`、`SKU_NOT_FOUND`、`DUPLICATE_EVENT`、`INVENTORY_INSUFFICIENT`、`ANALYSIS_CANCELLED`、`ANALYSIS_FAILED`、`BENCHMARK_UNAVAILABLE` 等），Warning 必须含 `code`、`severity`、`message`、`fields`、是否阻断五要素。

#### Scenario: UI 依据 code 而非文案判断
- **WHEN** 结果携带 Warning
- **THEN** `code` 为稳定英文标识，中文 `message` 仅用于展示

### Requirement: FakeEngine

系统 SHALL 提供 fixture 驱动的 `FakeEngine`，与真实引擎实现同一 `WarehouseEngine` Protocol（`validate_dataset` / `analyze` / `list_capabilities`），从 `tests/fixtures/fake-analysis.json` 读取固定结果返回。

#### Scenario: A 的工作台接入
- **WHEN** A 以 `FakeEngine.from_fixture(...)` 替换真实引擎调用 `analyze`
- **THEN** 返回结构完整、可序列化的 `AnalysisResult`，界面不依赖 B 的未完成计算器代码

### Requirement: 公式口径冻结

系统 SHALL 在 M0 产出公式口径冻结文档，覆盖 KPI（期初/期末库存、COGS、退货、盘点、负库存处理）、ABC（排序指标、累计占比、并列值归属）、库龄（左闭右开区间、缺失日期降级）、呆滞（观察窗口、无出库天数、排除条件）、补货（安全库存、服务水平假设）、预测与误差（数据集、切分、MAPE/MAE/RMSE、降级条件），每个口径携带 `formula_version`。

#### Scenario: 未冻结口径不得进入默认结果
- **WHEN** 某口径在文档中标记为"未冻结"
- **THEN** 引擎不将其写入默认 `AnalysisResult`，只能以实验输出存在

### Requirement: 黄金数据与边界数据

系统 SHALL 提供最小黄金数据（固定 `input.json` + `expected.json`，含预期数值、分类、排序、Warning 与容差声明）和边界数据集（空数据、零需求、负库存、重复事件、非法单位、缺失批次）。

#### Scenario: 引擎读取 fixture 通过 Schema 校验
- **WHEN** CI 执行 `FakeEngine`/引擎读取 `tests/fixtures/golden/*/input.json`
- **THEN** 通过 `analysis-request.schema.json` 校验且结果字段完整

### Requirement: 测试与 CI 门禁

系统 SHALL 建立 pytest 正反例契约测试（合法/非法请求各至少覆盖必填字段、类型、枚举、精度）、Hypothesis 库存守恒属性测试草案、Ruff/mypy 检查与性能脚本占位，并在 CI 中作为 B 侧 job 执行。

#### Scenario: 契约破坏被 CI 阻断
- **WHEN** 有人删除 `AnalysisRequest` 已有字段或改变类型而不提升 `schema_version`
- **THEN** 契约测试与 CI 失败，阻止合并

### Requirement: Skill 骨架与兼容矩阵

系统 SHALL 建立 `skills/{kpi,abc-aging,replenishment,forecasting,benchmark}/SKILL.md` 骨架（名称、版本、输入/输出 Schema 引用、错误码、降级策略占位）、Skill manifest（绑定 engine 版本范围）以及 `docs/compatibility-matrix.md` 初版。

#### Scenario: Skill 不越权
- **WHEN** 评审 Skill 定义
- **THEN** Skill 只编排参数校验、计算顺序与结果组合，不执行 Shell、不访问用户文件、不下载代码
