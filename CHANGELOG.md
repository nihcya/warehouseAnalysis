# 更新日志（CHANGELOG）

本文件遵循 Keep a Changelog 风格，版本号遵循语义化版本（SemVer），日期格式为 YYYY-MM-DD。
本文件是开发者 B 侧的变更记录：M0 的 contracts、engine、Skill、公式文档等组件由 B 并行交付，在此统一记录；开发者 A 侧组件（Desktop、Control API、Local DB 等）的变更由 A 侧记录。

## [0.2.0] - 2026-08-29 - 未发布（M1：KPI/COGS 引擎实现）

### Added

- 重放内核：`packages/warehouse-engine/src/warehouse_engine/replay.py`——事件按 `(move_date, occurred_at, event_id)` 升序重放、期间归属 `[start_date, end_date)`、冲销引用链解析（撤销的撤销 = 复原）、盘点余额替换、调拨 no-op、负余额跟踪（`NEGATIVE_BALANCE`）与移动加权平均成本状态。
- 九个公式实现：`calculators/inventory_kpi.py` 落地 F-KPI-001~008（期初/期末库存、出库量、动销率、库存价值、周转率、周转天数、覆盖天数）与 F-COGS-001（COGS，移动加权平均）；`WarehouseEngine.analyze` 由占位返回升级为真实计算路径（先校验后计算；abc-aging/replenishment/forecasting/benchmark 四类维持 M0 占位并以 `ANALYSIS_PLACEHOLDER` 标注）。
- 数据质量报告：contracts 新增 `DataQualityEntry`；`AnalysisResult.data_quality` 按 Warning 码汇总（计数 + 明细，条目按 code 字典序，确定性）随结果返回。
- contracts 新增可选字段（非破坏，schema_version 保持 1.0）：`MovementRecord.reversal_of`（冲销引用）、`ResultMetric.reason`（null/降级原因标注）、`AnalysisResult.data_quality`（数据质量报告）。
- 黄金数据指标层：`tests/fixtures/golden/v0.1.0/` 由 M0 校验层扩展至指标数值层（含推导说明与容差声明）；新增 `tests/fixtures/golden/v0.2.0/`（历史盘点/冲销期初、期末 0 与负余额、退货冲减为负、UNIT_COST_MISSING 价值回退、期内冲销等边界）。
- 性能基线（`scripts/perf_bench.py`，seed=20260829）：1 万行 analyze 1.49/1.80 秒；10 万行 analyze 15.80/12.38 秒、全链路（build+validate+analyze+digest）约 36 秒；100 万行 analyze 171.25 秒（仅 1 次实测，阈值 M3 冻结）。M1 阈值：1 万行 analyze ≤5 秒；10 万行 analyze ≤40 秒、全链路 ≤60 秒。
- wheel 首次交付：`dist/` 本地构建 `warehouse_engine-0.2.0` 与 `contracts_python-0.2.0` 两个 wheel + sdist（共 4 个产物）及 `dist/SHA256SUMS`；两 wheel 一起分发，独立环境安装验证通过。

### Changed

- 版本提升：warehouse-engine 0.1.0 → 0.2.0、contracts-python 0.1.0 → 0.2.0；`formula_version` 保持 0.1.0（口径已冻结，M1 未变更任何规则）。
- 校验增强：冲销引用缺失/成环与盘点实盘数量非正新增为阻断校验（`DATA_VALIDATION_FAILED`）。
- Skill 对齐：`skills/kpi/SKILL.md` 升至 0.2.0（implemented），公式步骤从占位改为 engine 0.2.0 实现引用；`skills/manifest.json` kpi engine 范围改为 `>=0.2.0,<0.3.0`，四个占位 Skill 放宽为 `>=0.1.0,<0.3.0`；`docs/compatibility-matrix.md` 同步 Engine 0.2.0 与 Skill bundle 0.2.0。

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
