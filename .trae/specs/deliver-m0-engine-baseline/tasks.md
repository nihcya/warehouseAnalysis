# Tasks：开发者 B M0 引擎基线

依据：`开发规划与协作需求文档.md` V2.0 §21.1/§22.2、`开发需求-B引擎Skill.md` §7 M0、`项目开发文档评审报告.md` §8 P0-2/P0-3。

- [x] Task 1: 冻结标准化数据契约（contracts，B 主导部分）
  - [x] 1.1 建立 `packages/contracts-python` 包：`enums.py` 定义 `MoveType`（入库/出库/退货/报废/调拨/盘点/冲销）、`EventSource`（IMPORT/DESKTOP/MINI_PROGRAM/ADJUSTMENT）、B 侧最小错误码、`WarningSeverity`
  - [x] 1.2 `analysis.py` 定义 `AnalysisRequest`（schema_version、run_id、start/end_date、warehouse_ids、筛选、参数）、`EngineDataset`（标准化列：sku_id、occurred_at、move_type、quantity、unit_cost、warehouse_id、lot_id、source、lead_time_days、on_hand_qty）、`AnalysisResult`、`ValidationReport`、`Warning`
  - [x] 1.3 冻结数量/金额精度（Decimal、scale、币种、四舍五入规则）与日期/时间格式（YYYY-MM-DD、UTC ISO 8601），写入 contracts 字段约束
  - [x] 1.4 导出 `packages/contracts-schema/analysis-request.schema.json` 与 `analysis-result.schema.json`，并保证与 Pydantic 模型一致（脚本生成 + CI 校验，`tests/contract/test_schema_sync.py` 看护）
  - [ ] 1.5 与 A 共同评审五份公共文件中 B 相关的三份（enums.py、analysis.py、两个 schema）；`sync-envelope.schema.json` 与 `openapi.json` 参与评审但不单方面产出 —— **B 侧文件已产出，评审会议待 A 安排（见 docs/m0-handover-b.md §8）**

- [x] Task 2: 公式口径冻结文档（formula_version 0.1 草案）
  - [x] 2.1 KPI/COGS 口径：期初/期末库存、COGS、退货、盘点、负库存处理规则（docs/formula-spec.md §3）
  - [x] 2.2 ABC/库龄/呆滞口径：排序指标、累计占比边界（80%/95%）、并列值归属；库龄左闭右开区间、缺失日期 Warning；呆滞观察窗口与排除条件（§4-6）
  - [x] 2.3 补货/预测/误差口径：安全库存与服务水平假设、参数缺失返回 PARAM_MISSING；预测训练/预测窗口、MAPE/MAE/RMSE 定义、样本不足降级规则（§7-8）
  - [x] 2.4 为每个口径分配公式标识与版本（F-* 编号）；明确"未冻结口径不得进入默认结果"
  - [ ] 2.5 与 A 走查一遍口径文档，确认无 UI 侧自行实现公式的空间 —— **草案完成，走查签字待 A（见 docs/m0-handover-b.md §8）**

- [x] Task 3: warehouse-engine 包骨架（uv workspace）
  - [x] 3.1 根 `pyproject.toml` 配置 uv workspace；`packages/warehouse-engine` 独立包，依赖仅 contracts-python + 锁版本的 pandas/numpy/scipy/statsmodels + 标准库
  - [x] 3.2 `engine.py` 实现 `WarehouseEngine`（M0 阶段：validate_dataset 可用、analyze 走校验+占位结果）、`contracts.py` 仅引用公共类型、`errors.py` 错误码→异常映射、`result.py` 结构化结果
  - [x] 3.3 `validation/` 输入校验空壳（字段、单位、日期、重复事件、负库存规则占位）；`calculators/` 五个模块空壳（inventory_kpi、abc_aging、replenishment、forecasting、benchmark_compare）

- [x] Task 4: FakeEngine 与最小 fixture
  - [x] 4.1 `tests/fixtures/golden/v0.1.0/input.json` + `expected.json`：最小样例，含预期校验结果、Warning 与容差声明（指标层数值随 M1 补充，已在 expected 注明）
  - [x] 4.2 `tests/fixtures/edge/`：空数据、零需求、负库存、重复事件、非法单位（精度违规）、缺失批次、边界日期样例（7 份）
  - [x] 4.3 `FakeEngine`：`from_fixture()` 加载 `tests/fixtures/fake-analysis.json`，实现同一 Protocol，返回结构完整的 `AnalysisResult`；`list_capabilities()` 返回占位 `CapabilityDescriptor`
  - [ ] 4.4 验证合并门槛：FakeEngine 可被 A 工作台调用展示结果；引擎读取 golden fixture 通过 Schema 校验 —— **Schema 校验项已由 test_golden/test_fixture_schema 验证；A 工作台实际接入待联调（见 docs/m0-handover-b.md §4、§8）**

- [x] Task 5: Skill 目录与 manifest
  - [x] 5.1 `skills/{kpi,abc-aging,replenishment,forecasting,benchmark}/SKILL.md` 骨架：名称、版本、用途、输入/输出 Schema 引用、公式步骤占位、错误码、数据不足行为、降级策略
  - [x] 5.2 Skill manifest：每个 Skill 绑定的 engine 版本范围、配置参数与默认值占位（skills/manifest.json）
  - [x] 5.3 `docs/compatibility-matrix.md` 初版（Desktop/API/Engine/Skill bundle/Local DB schema）与 `CHANGELOG.md`

- [x] Task 6: 测试与 CI 入口
  - [x] 6.1 `tests/contract/` 契约正反例：合法最小请求通过；必填缺失、类型错误、枚举非法、精度违规、负数量各返回 `DATA_VALIDATION_FAILED` 且 details 定位字段
  - [x] 6.2 Hypothesis 库存守恒属性测试草案：入库-出库守恒、数量非负、相同输入结果一致（tests/engine/test_conservation.py）
  - [x] 6.3 Ruff + mypy 配置接入 B 侧包；性能基准脚本占位 scripts/perf_bench.py（1 万/10 万/100 万流水入口，M0 不设阈值）
  - [x] 6.4 `.github/workflows/engine-ci.yml` B 侧 CI job：uv sync → pytest → ruff → mypy → schema 一致性检查（重导出 + git diff --exit-code）

- [x] Task 7: M0 合并门槛验收（G2 前置）
  - [ ] 7.1 与 A 联调验证 §22.2 合并门槛：A 工作台调用 FakeEngine 展示结果；B 引擎读同一 fixture 通过 Schema 校验；JSON Schema 与 Python 类型 CI 一致 —— **后两项已验证（47 测试全绿）；A 工作台接入待联调（见 docs/m0-handover-b.md §4）**
  - [x] 7.2 输出 M0 交接说明：安装命令、公开入口、依赖版本、支持 Schema、公式版本、已知限制、回滚版本（docs/m0-handover-b.md）
  - [x] 7.3 对照评审报告 P0-2/P0-3 自查并记录缺口（缺口转入 M1 任务，不静默降级；见 docs/m0-handover-b.md §5）

# Task Dependencies

- Task 2、Task 3 依赖 Task 1（契约先行）
- Task 4、Task 5 依赖 Task 3（包骨架存在）
- Task 6 依赖 Task 1、Task 4（有契约与 fixture 才能写正反例）
- Task 7 依赖 Task 1-6 全部完成
- Task 1.5、Task 2.5、Task 7.1 需要与 A 协作，存在外部协调点（已列入 docs/m0-handover-b.md §8 待办）

# 完成状态（2026-08-29）

- B 侧可独立完成的全部子任务已完成：pytest 47 passed、ruff/mypy 全绿、Schema 无漂移。
- 剩余 3 项（1.5、2.5、4.4/7.1 的 A 联调部分）为双人协作项，需开发者 A 参与，已汇总至 `docs/m0-handover-b.md` 第 8 节。
