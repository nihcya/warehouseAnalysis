# 兼容性矩阵（compatibility-matrix）

| 项 | 值 |
|---|---|
| 文档版本 | 0.2.0（M1 更新） |
| 维护人 | 开发者 B（维护 Engine / Skill bundle 行及 contracts 引擎侧行；其余行由开发者 A 确认） |
| 数据来源 | `开发规划与协作需求文档.md` V2.0 §21.5、`开发需求-B引擎Skill.md` |

## 1. 组件版本与兼容要求

| 组件 | 当前版本 | 兼容要求 |
|---|---|---|
| Desktop | `1.0.0` | 支持 API `v1`，引擎 `1.x` |
| Control API | `1.0.0` | 支持 Desktop `1.0.x` |
| Engine | `0.2.0` | 支持 contracts `1.x`；公式口径 `0.1.0`（冻结）；首个版本化 wheel 已发布（`dist/` + `SHA256SUMS`） |
| Skill bundle | `0.2.0` | 绑定 engine 主版本；kpi（implemented）`>=0.2.0,<0.3.0`，四个占位 Skill `>=0.1.0,<0.3.0`（见 `skills/manifest.json`） |
| contracts | `1.0`（contracts-python 0.2.0） | `analysis-request.schema.json` / `analysis-result.schema.json` 与 `contracts-python` Pydantic 模型一致；0.2.0 新增可选字段（`reversal_of`、`ResultMetric.reason`、`AnalysisResult.data_quality`）为非破坏变更；变更需 A、B 共同评审 |
| Local DB schema | `3` | Alembic 可从 `2` 升级，禁止降级覆盖 |

## 2. 版本规则

- **Patch**：只修复问题，不改变 Schema。
- **Minor**：允许增加可选字段和功能。
- **Major**：才允许删除字段或改变公式口径；删除公共字段、改类型或改单位必须提升 `schema_version`，补正反例与迁移说明，并经 A、B 共同评审。
- **公式口径变更**：任何规则变更必须提升 `formula_version`，重新生成黄金结果，并说明对旧报告的影响。
- **不兼容检测**：工作台启动时检查 API、引擎和 Skill 兼容性，不兼容时显示明确升级提示。
- **发布顺序**：fixture/Schema → engine/Skill → A 适配层 → UI/报告 → staging → release；每个版本打 Git Tag 并记录客户端、API、引擎、Skill 与数据 Schema 版本。

## 3. 当前状态（M1）

- Engine `0.2.0`：KPI/COGS（F-KPI-001~008、F-COGS-001，formula-spec §3）已真实实现并进入默认 `AnalysisResult`（重放内核 + 数据质量报告）；abc-aging/replenishment/forecasting/benchmark 四类维持 M0 占位。
- Skill bundle `0.2.0`：`kpi` Skill 升为 implemented（绑定 engine `>=0.2.0,<0.3.0`）；其余四个 Skill 仍为 skeleton（绑定 `>=0.1.0,<0.3.0`，兼容 0.1.0 FakeEngine 与 0.2.0 真实引擎）。
- 公式版本 `0.1.0`：M0 已冻结（A/B 签字），M1 未变更；contracts `schema_version` 保持 `1.0`（contracts-python 包 0.2.0 仅新增可选字段，非破坏）。
- 首个版本化 wheel 已交付：`dist/`（`warehouse_engine-0.2.0` 与 `contracts_python-0.2.0` 两 wheel + sdist + `SHA256SUMS`）；回滚方式见 `docs/m1-handover-b.md` §7。
- Desktop、Control API、Local DB schema 由开发者 A 并行开发中；本矩阵由 B 维护 Engine、Skill bundle 与 contracts（引擎侧）行，其余行先按项目规范目标值登记，待 A 确认后更新。
- 下次更新触发条件：任一组件版本变更、`schema_version` 或 `formula_version` 提升、兼容范围调整。

## 4. 维护说明

- 每个可发布版本必须更新本矩阵（见 `开发规划与协作需求文档.md` §21.5）。
- Skill 与 engine 的绑定关系以 `skills/manifest.json` 的 `engine_version_range` 为准。
- 历史版本对应关系随仓库根 `CHANGELOG.md` 记录追溯。
