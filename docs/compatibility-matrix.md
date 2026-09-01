# 兼容性矩阵（compatibility-matrix）

| 项 | 值 |
|---|---|
| 文档版本 | 0.3.0（M2 更新） |
| 维护人 | 开发者 B（维护 Engine / Skill bundle 行及 contracts 引擎侧行；其余行由开发者 A 确认） |
| 数据来源 | `开发规划与协作需求文档.md` V2.0 §21.5、`开发需求-B引擎Skill.md` |

## 1. 组件版本与兼容要求

| 组件 | 当前版本 | 兼容要求 |
|---|---|---|
| Desktop | `1.0.0` | 支持 API `v1`，引擎 `1.x` |
| Control API | `1.0.0` | 支持 Desktop `1.0.x` |
| Engine | `0.3.0` | 支持 contracts `1.x`；公式口径 `0.1.0`（冻结）；M2 实现五类公式共 18 指标，移除 `ANALYSIS_PLACEHOLDER`；wheel 留 M3 发布（见 `docs/m2-handover-b.md` §1） |
| Skill bundle | `0.3.0` | 绑定 engine 主版本；kpi（implemented）`>=0.2.0,<0.4.0`，abc-aging/replenishment/forecasting/benchmark 四个 Skill 升 implemented（均 `>=0.3.0,<0.4.0`，见 `skills/manifest.json`） |
| contracts | `1.0`（contracts-python 0.2.0） | `analysis-request.schema.json` / `analysis-result.schema.json` 与 `contracts-python` Pydantic 模型一致；M2 未新增字段，`schema_version` 保持 `1.0`（非破坏）；变更需 A、B 共同评审 |
| Local DB schema | `3` | Alembic 可从 `2` 升级，禁止降级覆盖 |

## 2. 版本规则

- **Patch**：只修复问题，不改变 Schema。
- **Minor**：允许增加可选字段和功能。
- **Major**：才允许删除字段或改变公式口径；删除公共字段、改类型或改单位必须提升 `schema_version`，补正反例与迁移说明，并经 A、B 共同评审。
- **公式口径变更**：任何规则变更必须提升 `formula_version`，重新生成黄金结果，并说明对旧报告的影响。
- **不兼容检测**：工作台启动时检查 API、引擎和 Skill 兼容性，不兼容时显示明确升级提示。
- **发布顺序**：fixture/Schema → engine/Skill → A 适配层 → UI/报告 → staging → release；每个版本打 Git Tag 并记录客户端、API、引擎、Skill 与数据 Schema 版本。

## 3. 当前状态（M2）

- Engine `0.3.0`：五类公式共 18 指标已真实实现并进入默认 `AnalysisResult`（F-KPI-001~008/F-COGS-001、F-ABC-001/F-AGE-001/F-STALE-001、F-REPL-001~003、F-FCST-001~002、F-BM-001）；移除 `ANALYSIS_PLACEHOLDER`；重放内核扩展为四个计算器供数（向后兼容）；实验模型经 `experimental/` 隔离、不进默认结果。
- Skill bundle `0.3.0`：五个 Skill 全部 implemented；kpi 兼容 `>=0.2.0,<0.4.0`，其余四个绑定 `>=0.3.0,<0.4.0`。
- 公式版本 `0.1.0`：M0 已冻结（A/B 签字），M2 未变更；contracts `schema_version` 保持 `1.0`（M2 零字段新增，非破坏）。
- wheel 不在 M2 产出（留 M3 发布门槛）；本地构建与独立环境验证步骤见 `docs/m2-handover-b.md` §1；回滚方式沿用 M1 的 wheel 回滚。
- Desktop、Control API、Local DB schema 由开发者 A 并行开发中；本矩阵由 B 维护 Engine、Skill bundle 与 contracts（引擎侧）行，其余行先按项目规范目标值登记，待 A 确认后更新。
- 下次更新触发条件：任一组件版本变更、`schema_version` 或 `formula_version` 提升、兼容范围调整。

## 4. 维护说明

- 每个可发布版本必须更新本矩阵（见 `开发规划与协作需求文档.md` §21.5）。
- Skill 与 engine 的绑定关系以 `skills/manifest.json` 的 `engine_version_range` 为准。
- 历史版本对应关系随仓库根 `CHANGELOG.md` 记录追溯。
