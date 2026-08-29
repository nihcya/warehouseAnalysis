# 更新日志（CHANGELOG）

本文件遵循 Keep a Changelog 风格，版本号遵循语义化版本（SemVer），日期格式为 YYYY-MM-DD。
本文件是开发者 B 侧的变更记录：M0 的 contracts、engine、Skill、公式文档等组件由 B 并行交付，在此统一记录；开发者 A 侧组件（Desktop、Control API、Local DB 等）的变更由 A 侧记录。

## [0.1.0] - 2026-08-29 - 未发布（M0 骨架）

### Added

- `packages/contracts-python` 0.1.0：`enums.py`（`MoveType`、`EventSource`、B 侧最小错误码、`WarningSeverity`）与 `analysis.py`（`AnalysisRequest`、`EngineDataset`、`AnalysisResult`、`ValidationReport`、`Warning`）。
- `packages/contracts-schema`：引擎侧 JSON Schema `analysis-request.schema.json` 与 `analysis-result.schema.json`（contracts 1.0）。
- `packages/warehouse-engine` 0.1.0 骨架：`engine.py`、`contracts.py`、`result.py`、`errors.py`、`validation/` 与 `calculators/`（五个计算器空壳）。
- 五个 Skill 骨架：`skills/{kpi,abc-aging,replenishment,forecasting,benchmark}/SKILL.md`（状态 skeleton，绑定 engine `>=0.1.0,<0.2.0`）。
- `skills/manifest.json`：Skill 清单（`manifest_version` 1.0）。
- `docs/compatibility-matrix.md`：兼容矩阵初版（Desktop / Control API / Engine / Skill bundle / contracts / Local DB schema）。
- `docs/formula-spec.md` 0.1.0 草案：KPI/COGS、ABC/库龄/呆滞、补货、预测与误差公式口径冻结文档（含 `formula_version` 标识）。

### 说明

- 本版本为 M0 骨架，公式未冻结，未打 Git Tag、未产出 wheel，不作为生产版本分发。
- FakeEngine、黄金数据、边界数据、契约测试与 CI 入口由 B 侧并行任务交付，完成后在本文件补充记录。
- 兼容性要求详见 `docs/compatibility-matrix.md`。
