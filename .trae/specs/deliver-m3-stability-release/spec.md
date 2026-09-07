# Spec：B 侧 M3 稳定性与发布（deliver-m3-stability-release）

| 项 | 值 |
|---|---|
| change-id | `deliver-m3-stability-release` |
| 负责人 | 开发者 B（李柏霖） |
| 需求来源 | `docs/开发需求-B引擎Skill.md` §7 M3、§8.3 完成定义、§9 交接包；`docs/m2-handover-b.md` §1/§4 遗留项 |
| 基线 | origin/master `9ef2d73`（含 A 侧 M3 PR #21/#23 与 B 侧 M2 PR #5/#14） |
| 工作分支 | `feature/b-m3-stability-release`（自 origin/master 拉出，在 worktree `f:\publicPro\warehouseAnalysis-b-m1` 开发） |
| 状态 | 实现完成；分支已推送 origin，PR 创建待执行（gh 认证失效，需 `gh auth login` 或经 GitHub 网页创建） |

## 1. 背景与目标

M2 已交付五类公式 18 指标（engine 0.3.0，formula_version 0.1.0 冻结不变，contracts `schema_version` 保持 1.0 零字段变更）。M3 是 B 侧的**发布里程碑**，按需求 §7 M3 完成三件事：

1. **测试完备**：完成黄金/边界/属性/性能/重复运行测试（作为发布门槛全部通过并留证）。
2. **发布产物**：生成 engine wheel、SHA-256 清单、依赖清单、SBOM 片段和变更日志。
3. **staging 验证**：使用脱敏数据确认版本兼容、回滚和结果可追溯。

同时清偿 M2 交接文档中明确挂到 M3 的三项遗留：wheel 构建（M2 未产出）、contracts 零漂移复导出证明、性能复测（对照 M1 阈值无回归）。

**非目标**（明确排除，防止范围蔓延）：

- 不修改任何公式口径（formula_version 保持 `0.1.0`）、不新增指标、不动 contracts 字段（schema_version 保持 `1.0`）。
- 不做 A 侧产物（Desktop 安装包、托盘 Agent、control-plane）——已由 `deliver-m3-agent-sync-release`（PR #21）交付；B 侧只负责 engine/Skill 域内的发布资产与 staging 联调中 B 的部分。
- 不做 `docs/release-checklist.md` 的安装包验收（A 侧职责），但 staging 验证中 A/B 交叉验证项按 §6 协作。

## 2. 发现的问题（P0 前置修复）

在准备本 spec 时发现 master 存在**版本源漂移**：

- `packages/warehouse-engine/pyproject.toml`：`version = "0.2.0"`，描述仍为「M1：KPI/COGS 实装」；
- `packages/warehouse-engine/src/warehouse_engine/__version__.py`：`ENGINE_VERSION = "0.3.0"`；
- `tests/engine/test_golden.py:109`：断言 `result.engine_version == "0.3.0"`。

wheel 以 pyproject 为版本源构建，若不修复将产出 `warehouse_engine-0.2.0` 的 wheel，与运行时 0.3.0 矛盾，直接违反发布门槛。**该修复是 Task 0 的 P0 项**（版本对齐到 0.3.0 + 描述更新 + 新增一致性守护测试），不是口径变更。

## 3. 需求细化（Requirement → Design）

### R1 测试完备（需求 §7 M3 第 1 条）

现状盘点（master 已有）：黄金数据 v0.1.0–v0.4.0、边界 fixture 5 份、Hypothesis 守恒属性（`test_conservation.py`）、性能脚本 `scripts/perf_bench.py`、schema 漂移 CI 门禁（`scripts/export_schemas.py` + `verify_schema.py`）。

M3 补强项：

- **R1.1 重复运行确定性**：新增测试证明同一输入连续 N 次（≥3）analyze 输出逐字节一致（serialize 后哈希比对），跨进程重复亦一致（复用 M1 digest 确定性机制）。
- **R1.2 版本一致性守护**：新增测试断言 pyproject 版本 == `ENGINE_VERSION` == 黄金数据引用的 engine_version，防止本次发现的漂移再次发生。
- **R1.3 contracts 零漂移证据**：重跑 `scripts/export_schemas.py`，证明 schema 与 `packages/contracts-schema/` 快照零 diff，作为「M3 未动契约」的归档证据。
- **R1.4 性能复测**：`perf_bench.py` 1 万行 / 10 万行档位各跑 ≥3 轮，对照 M1 阈值（analyze ≤5s / ≤40s、全链路 ≤60s）确认无回归，结果写入 `docs/m3-handover-b.md`（100 万行档位选跑，仅记录不设阈）。
- **R1.5 全量回归**：workspace 全部测试通过（M2 基线 273 passed / 17 skipped 为参考，允许新增用例数增长，不允许任何既有用例转红或被删除/跳过）。

### R2 发布产物（需求 §7 M3 第 2 条、§9 交接包）

- **R2.1 wheel 构建**：构建 `contracts_python-0.3.0` 与 `warehouse_engine-0.3.0` 两个 wheel（+ sdist，共 4 产物），输出到 `dist/`。
  - 版本号修正：contracts-python 当前 pyproject 0.2.0 → 0.3.0（M2 交付时按惯例应随 engine 同步，交接文档 §1 示例命令亦写 `contracts_python-0.3.0`，说明 M2 即已预期 0.3.0；本次对齐）。
  - 独立环境验证：在全新 venv 中仅装两个 wheel，跑黄金数据冒烟（不依赖 workspace 源码），证明 wheel 自洽。
- **R2.2 SHA-256 清单**：`dist/SHA256SUMS`（沿用 M1 格式），覆盖全部 4 个产物。
- **R2.3 依赖清单**：导出 `uv.lock` 摘要或 `pip freeze` 等效清单（标注 Python 3.11、核心依赖 pandas/numpy/scipy/statsmodels 版本区间），随交接包提供。
- **R2.4 SBOM 片段**：新增 `scripts/export_sbom.py` 生成 engine 域 SBOM 片段（格式 CycloneDX JSON，tools 级实现即可，不引入重型工具链；覆盖两个发布包及其依赖），输出 `dist/sbom-engine-0.3.0.json`。
- **R2.5 变更日志**：`CHANGELOG.md` 由「未发布」转为正式发布条目（0.3.0 定稿日期），并补 M3 段落（发布产物、稳定性证据、staging 结论摘要）。
- **R2.6 交接文档**：新增 `docs/m3-handover-b.md`（对照 `docs/开发需求-B引擎Skill.md` §9 交接包清单逐项给出行号/文件路径），更新 `docs/compatibility-matrix.md`（wheel 发布状态、日期、B 侧行）。

### R3 staging 验证（需求 §7 M3 第 3 条）

使用脱敏数据（构造 staging 数据集：真实分布特征 + 字段脱敏，禁止使用任何真实客户数据）完成：

- **R3.1 版本兼容**：staging 环境按 `docs/compatibility-matrix.md` 记录的版本组合运行——engine 0.3.0 wheel + contracts 0.3.0 wheel + Skill bundle 0.3.0，工作台（A 侧 1.0.0）调用链跑通一次完整分析，确认兼容声明成立。
- **R3.2 回滚演练**：wheel 0.3.0 → 0.2.0 回滚（pip 降级安装），验证：回滚后 contracts schema 快照仍匹配、黄金数据 v0.1.0/v0.2.0 通过、Skill manifest `engine_version_range` 拒绝不兼容组合的守护逻辑生效；再升级回 0.3.0 复跑黄金全绿。
- **R3.3 结果可追溯**：同一 staging 数据集用 0.3.0 与回滚后 0.2.0 各跑一次，结果文件（含 engine_version/formula_version/digest）归档，证明任何历史报告可凭版本号 + digest 复现或解释差异（公式口径未变时结果应一致，若引擎行为有差异须给出逐条解释）。
- **R3.4 staging 记录**：全程留档 `docs/m3-staging-log-b.md`（环境、脱敏数据指纹、每步命令、结果哈希、异常与结论），作为 §9 交接包附件。

## 4. 边界与协作（A/B 接口）

| 项 | B 侧职责 | A 侧职责 |
|---|---|---|
| engine/contracts wheel + SHA256SUMS + SBOM + 依赖清单 | 产出与维护 | 消费（工作台打包引用） |
| Skill bundle 0.3.0 与 manifest | 维护 | 启动时兼容检查（已有） |
| staging 联调 | 提供脱敏数据集与验证脚本、B 域结论 | workbench 端到端操作 |
| release-checklist.md 安装包验收 | 不参与 | 执行 |
| compatibility-matrix.md | 更新 Engine/Skill/contracts 行 | 确认 Desktop/Control API 行 |

协调点（沿用 M2 §6 惯例，在 m3-handover-b.md §A 侧确认项中登记）：staging 时间窗口、A 侧 1.0.0 与 engine 0.3.0 联调结论回填。

## 5. 验收标准（Definition of Done，对照需求 §8.3）

1. [x] workspace 全量测试通过，新增：确定性测试、版本一致性测试、SBOM 导出测试、wheel 独立环境冒烟脚本/测试；既有用例零删除零跳过。
2. [x] `scripts/export_schemas.py` 复导出零 diff（契约零改动证据归档）。
3. [x] `dist/` 含 4 个产物 + `SHA256SUMS` + `sbom-engine-0.3.0.json` + 依赖清单文件；SHA-256 复算一致；独立 venv 仅装 wheel 跑通黄金冒烟。
4. [x] perf_bench 1 万/10 万行 ≥3 轮全部达标（≤5s / ≤40s、全链路 ≤60s），数据落档。
5. [x] staging 三项验证（兼容/回滚/可追溯）完成并留档 `docs/m3-staging-log-b.md`，结论明确。
6. [x] `CHANGELOG.md` 0.3.0 正式定稿、`docs/m3-handover-b.md`、`docs/compatibility-matrix.md` 更新完毕，§9 交接包逐项可索引。
7. [ ] PR 自 origin/master 拉出的 `feature/b-m3-stability-release` 合并，遵循 §26.2 PR 模板，CI 绿。（分支已推送；PR 待创建——gh 认证失效）

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 沙箱禁止写 `.venv` dist-info，wheel 独立环境验证可能被拦 | 验证脚本设计为可用 `--target` 目录安装或由用户手动执行；脚本交付不阻塞，执行记录可后补 |
| statsmodels 等依赖使 wheel 体积/依赖解析复杂 | wheel 只声明依赖区间，安装验证走 uv/pip 常规解析；SBOM 记录解析结果 |
| staging 需 A 侧 1.0.0 环境配合，时间可能错位 | R3.1 的 B 域部分（wheel + Skill manifest 校验 + 黄金冒烟）可先行独立完成，端到端联调项在 handover 文档标记「待 A 回填」 |
| 回滚演练 pip 降级触及 `.venv` 写入限制 | 回滚演练在 staging venv（非 workspace .venv）执行，天然规避沙箱限制 |

## 7. 涉及文件清单

新增：`scripts/export_sbom.py`、`tests/engine/test_determinism.py`、`tests/engine/test_version_consistency.py`、`tests/engine/test_sbom_export.py`（或并入现有测试文件，实现期定）、`docs/m3-handover-b.md`、`docs/m3-staging-log-b.md`、`dist/` 产物（不入库，发布时随 release 附件）。

修改：`packages/warehouse-engine/pyproject.toml`（0.3.0 对齐 + 描述）、`packages/contracts-python/pyproject.toml`（0.3.0 对齐）、`CHANGELOG.md`、`docs/compatibility-matrix.md`、（如缺）`.gitignore` 确认 dist/ 忽略策略。
