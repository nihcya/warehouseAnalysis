# 兼容性矩阵（compatibility-matrix）

| 项 | 值 |
|---|---|
| 文档版本 | 0.1.0（M0 初版） |
| 维护人 | 开发者 B（维护 Engine / Skill bundle 行及 contracts 引擎侧行；其余行由开发者 A 确认） |
| 数据来源 | `开发规划与协作需求文档.md` V2.0 §21.5、`开发需求-B引擎Skill.md` |

## 1. 组件版本与兼容要求

| 组件 | 当前版本 | 兼容要求 |
|---|---|---|
| Desktop | `1.0.0` | 支持 API `v1`，引擎 `1.x` |
| Control API | `1.0.0` | 支持 Desktop `1.0.x` |
| Engine | `0.1.0` | 支持 contracts `1.x` |
| Skill bundle | `0.1.0` | 绑定 engine 主版本；当前范围 `>=0.1.0,<0.2.0`（见 `skills/manifest.json`） |
| contracts | `1.0` | `analysis-request.schema.json` / `analysis-result.schema.json` 与 `contracts-python` Pydantic 模型一致；变更需 A、B 共同评审 |
| Local DB schema | `3` | Alembic 可从 `2` 升级，禁止降级覆盖 |

## 2. 版本规则

- **Patch**：只修复问题，不改变 Schema。
- **Minor**：允许增加可选字段和功能。
- **Major**：才允许删除字段或改变公式口径；删除公共字段、改类型或改单位必须提升 `schema_version`，补正反例与迁移说明，并经 A、B 共同评审。
- **公式口径变更**：任何规则变更必须提升 `formula_version`，重新生成黄金结果，并说明对旧报告的影响。
- **不兼容检测**：工作台启动时检查 API、引擎和 Skill 兼容性，不兼容时显示明确升级提示。
- **发布顺序**：fixture/Schema → engine/Skill → A 适配层 → UI/报告 → staging → release；每个版本打 Git Tag 并记录客户端、API、引擎、Skill 与数据 Schema 版本。

## 3. 当前状态（M0）

- Engine 与 Skill bundle 处于 `0.1.0` 骨架（skeleton）：五个 Skill 已建立 `SKILL.md` 与 `skills/manifest.json`，绑定 engine `>=0.1.0,<0.2.0`；公式版本 `0.1.0` 未冻结（口径草案见 `docs/formula-spec.md`）。
- contracts 为 `1.0`：`packages/contracts-schema` 引擎侧 Schema 与 `packages/contracts-python` 模型的一致性由 CI 校验。
- Desktop、Control API、Local DB schema 由开发者 A 并行开发中；本矩阵由 B 维护 Engine、Skill bundle 与 contracts（引擎侧）行，其余行先按项目规范目标值登记，待 A 确认后更新。
- 下次更新触发条件：任一组件版本变更、`schema_version` 或 `formula_version` 提升、兼容范围调整。

## 4. 维护说明

- 每个可发布版本必须更新本矩阵（见 `开发规划与协作需求文档.md` §21.5）。
- Skill 与 engine 的绑定关系以 `skills/manifest.json` 的 `engine_version_range` 为准。
- 历史版本对应关系随仓库根 `CHANGELOG.md` 记录追溯。
