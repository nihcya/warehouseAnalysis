# Tasks：开发者 B M2 分析 Skill

依据：`开发需求-B引擎Skill.md` §7 M2、`docs/formula-spec.md` 0.1.0（已冻结）§4~§10、`docs/m1-handover-b.md` §6 已知限制（abc-aging/replenishment/forecasting/benchmark 四类占位待 M2 实现）。

- [ ] Task 0: 分支创建与 spec 骨架
  - [x] 0.1 `git fetch origin` 后从 `origin/master`（d7e1468）创建并切换到 `feature/b-m2-analysis-skills`
  - [x] 0.2 新建 `.trae/specs/deliver-m2-analysis-skills/{spec,tasks,checklist}.md`
  - [x] 0.3 `uv sync --all-packages --group dev` 跑通 M1 基线（pytest tests/engine tests/contract → 147 passed）
  - [x] 0.4 提交 `docs(spec): add M2 analysis-skills spec skeleton on feature/b-m2-analysis-skills`

- [ ] Task 1: 重放内核扩展（先写失败测试，依赖 Task 0）
  - [ ] 1.1 新增 `LotBalance` / `InboundLot` frozen dataclass
  - [ ] 1.2 `ReplayOutcome` 追加 `daily_out_by_sku`（`[start_date, end_date)` 逐日，零出库日补 0，日期升序）
  - [ ] 1.3 追加 `last_outbound_date_by_sku` / `first_inbound_date_by_sku`（全重放视界）
  - [ ] 1.4 追加 `lot_balances`（有 lot_id 的期末批次余额，FIFO 消耗）
  - [ ] 1.5 追加 `residual_inbound_by_sku`（FIFO 残余入库切片）与 `all_inbound_by_sku`（回退总体）
  - [ ] 1.6 追加 `invalid_date_events`（供上层发 `DATE_MISSING`）
  - [ ] 1.7 新增 `tests/engine/test_replay_m2.py`；确认 M1 既有 147 个测试无回归
  - [ ] 1.8 提交 `test(engine): ...` + `feat(engine): extend replay kernel ...`

- [ ] Task 2: abc-aging 计算器（依赖 Task 1）
  - [ ] 2.1 F-ABC-001：出库金额排序、两级稳定排序、累计占比归属、并列与全零边界、`NO_OUTFLOW`
  - [ ] 2.2 F-AGE-001：批次口径 + 无批次 FIFO 平均库龄、左闭右开五档、`DATE_MISSING`
  - [ ] 2.3 F-STALE-001：观察窗口、无出库天数回退、排除条件与 `excluded_reason`
  - [ ] 2.4 `UNIT_COST_MISSING` 与 KPI 跨计算器去重；新增 `tests/engine/test_abc_aging.py`
  - [ ] 2.5 提交 `test(engine): ...` + `feat(engine): implement F-ABC-001, F-AGE-001 and F-STALE-001 ...`

- [ ] Task 3: replenishment 计算器（依赖 Task 1）
  - [ ] 3.1 F-REPL-001 安全库存：σ_d（n−1，含零出库日）与 `Decimal.sqrt()`
  - [ ] 3.2 z 值表四档与表外最近档（差值相等取较低档）
  - [ ] 3.3 F-REPL-002/003 补货点与建议量（在途量扣减、下限 0）
  - [ ] 3.4 `PARAM_MISSING` 降级与参数优先级（dataset.replenishment > parameters > 默认）
  - [ ] 3.5 新增 `tests/engine/test_replenishment.py`；提交 `feat(engine): implement F-REPL-001~003 ...`

- [ ] Task 4: forecasting 计算器（依赖 Task 1）
  - [ ] 4.1 F-FCST-001：自然周聚合（周一为周首）、4 周移动平均基线、窗口按时间切分
  - [ ] 4.2 F-FCST-002：MAPE/WAPE/MAE/RMSE（`Decimal.sqrt()`，无 float）
  - [ ] 4.3 `n < 12` 强制降级 + `INSUFFICIENT_SAMPLES`（fields 含 sku_id 与 n）
  - [ ] 4.4 新增 `tests/engine/test_forecasting.py`；提交 `feat(engine): implement F-FCST-001~002 ...`

- [ ] Task 5: benchmark_compare 计算器（依赖 Task 1）
  - [ ] 5.1 基准八字段完整性校验，缺一即丢弃
  - [ ] 5.2 `(industry, region, metric)` 匹配与多命中取最新（并列按 source 字典序）
  - [ ] 5.3 无匹配返回 `BENCHMARK_UNAVAILABLE`（非阻断）
  - [ ] 5.4 版本化基准 fixture `tests/fixtures/benchmarks/v0.1.0.json` + `tests/engine/test_benchmark_compare.py`
  - [ ] 5.5 提交 `feat(engine): implement F-BM-001 with versioned benchmark fixtures`

- [ ] Task 6: experimental 隔离骨架（依赖 Task 4）
  - [ ] 6.1 新建 `experimental/{__init__,types,forecast_models}.py`（类型层隔离，独立结果类型）
  - [ ] 6.2 导入层隔离：`engine.py` 与四个计算器均不 import `experimental`
  - [ ] 6.3 `tests/engine/test_experimental_isolation.py`：默认路径不含实验输出且不 import `statsmodels`
  - [ ] 6.4 提交 `feat(engine): add experimental/ isolation skeleton ...`

- [ ] Task 7: analyze 接线与占位移除（依赖 Task 2-6）
  - [ ] 7.1 `analyze` 按固定顺序拼接五类指标（18 个），移除 `ANALYSIS_PLACEHOLDER`
  - [ ] 7.2 `list_capabilities` 描述去掉占位措辞；progress 阶段细化到 6 段
  - [ ] 7.3 `__version__.py`：`ENGINE_VERSION` 0.2.0 → 0.3.0，`FORMULA_VERSION` 保持 0.1.0
  - [ ] 7.4 更新 `tests/engine/test_analyze_integration.py` 的 5 处断言（9→18、占位转无占位、data_quality）
  - [ ] 7.5 提交 `feat(engine): wire four calculators into analyze and drop ANALYSIS_PLACEHOLDER`

- [ ] Task 8: 黄金数据与边界 fixture（依赖 Task 7）
  - [ ] 8.1 新增 `tests/fixtures/golden/v0.3.0/`（ABC 80/95 边界与并列、零出库、全零；库龄 0/30/60/90/180 边界、缺日期、无批次近似；呆滞 90 vs 91、阈值 1、新品豁免、停产排除）
  - [ ] 8.2 新增 `tests/fixtures/golden/v0.4.0/`（z 表四档、参数缺失、零需求、在途量；≥12 期含季节性与零需求、n<12 降级、切分边界；基准有匹配/无匹配）
  - [ ] 8.3 回填 `v0.1.0`/`v0.2.0` 的 `metrics`（9 个新增指标）与 `analyze_warnings`
  - [ ] 8.4 新增 6 份 edge fixture 并登记进 `tests/engine/test_edge_cases.py`
  - [ ] 8.5 `tests/engine/test_golden.py`：`GOLDEN_VERSIONS` 追加两版、`ALL_M2_FORMULA_IDS`（18）、占位断言转无占位、engine_version 0.3.0
  - [ ] 8.6 新增 `tests/engine/test_m2_integration.py`（"导入→分析→报告"联调自证）
  - [ ] 8.7 分 4 笔提交（v0.3.0 / v0.4.0 / 回填 / edge）

- [ ] Task 9: 文档与清单（依赖 Task 7、8）
  - [ ] 9.1 四个 SKILL.md 由 skeleton 转 implemented（对齐 `skills/kpi/SKILL.md` 范本）
  - [ ] 9.2 `skills/manifest.json`：version 0.2.0、status implemented、engine 范围 `>=0.3.0,<0.4.0`
  - [ ] 9.3 `CHANGELOG.md` 0.3.0（Added / Changed / Known limitations）
  - [ ] 9.4 `docs/compatibility-matrix.md` 同步 engine 0.3.0 与 Skill bundle 0.3.0
  - [ ] 9.5 新增 `docs/m2-handover-b.md`（照 M1 八节结构）
  - [ ] 9.6 分 2 笔提交（`docs(skills): ...`、`docs: ...`）

- [ ] Task 10: 验收与 PR（依赖全部）
  - [ ] 10.1 全量检查：`pytest`、`ruff check`、`mypy`、`export_schemas.py` 无 diff（契约零改动门禁证据）
  - [ ] 10.2 `perf_bench` 复测 1 万/10 万行，确认未劣化 M1 阈值（≤5s / ≤40s）
  - [ ] 10.3 确认未修改 A 侧代码；勾选本清单全部条目
  - [ ] 10.4 提交 `docs(spec): mark M2 tasks complete with verification evidence`，按 §26.2 PR 模板提 `feature/b-m2-analysis-skills` → `master`

# Task Dependencies

- Task 0 → 全部（分支先建立）
- Task 1 → Task 2、3、4、5（四个计算器都依赖重放内核新增采集项）
- Task 2、3、4、5 在 Task 1 就绪后可并行开发，各占独立提交互不阻塞
- Task 4 → Task 6（实验模型是预测的实验延伸）
- Task 2-6 → Task 7 → Task 8 → Task 9 → Task 10
- Task 6 与 Task 8 无相互依赖，可并行
