# Checklist：开发者 B M0 引擎基线

验证时间：2026-08-29；验证方式：pytest 47 passed / ruff 全绿 / mypy 18 文件无问题 / Schema 哈希比对无漂移。

- [x] `contracts/enums.py` 包含 MoveType 七种操作、EventSource 四种来源、B 侧最小错误码（19 个）与 WarningSeverity，无中文标识作为判断依据
- [x] `contracts/analysis.py` 的 AnalysisRequest/EngineDataset/AnalysisResult/ValidationReport/Warning 字段齐全，金额为 Decimal 语义（序列化字符串）、日期为 YYYY-MM-DD、时间为 UTC ISO 8601
- [x] AnalysisResult 携带 run_id、engine_version、formula_version、数据期间与输入摘要（InputSummary 含 dataset_digest）
- [x] `analysis-request.schema.json` 与 `analysis-result.schema.json` 存在且和 Pydantic 模型一致（tests/contract/test_schema_sync.py 通过，CI 重导出门禁看护）
- [x] Warning 含 code、severity、message、fields、是否阻断五要素
- [x] 公式口径文档（docs/formula-spec.md 0.1.0-draft）覆盖 KPI、COGS、ABC、库龄、呆滞、补货、预测与误差七类口径，每个口径有 F-* 公式标识与版本，未冻结口径明确排除出默认结果（正式冻结待 A 走查签字）
- [x] warehouse-engine 包仅依赖 contracts-python、pandas/numpy/scipy/statsmodels（uv.lock 锁版本）与标准库，无 UI/HTTP/ORM/SQLite 依赖
- [x] WarehouseEngine 实现 validate_dataset/analyze/list_capabilities，符合 Protocol 签名，不修改调用方传入数据（test_engine_interface.py 验证）
- [x] FakeEngine.from_fixture 可加载固定 fixture 并返回结构完整 AnalysisResult（test_fake_engine.py 验证；A 工作台实际接入联调待办，见 docs/m0-handover-b.md §8）
- [x] golden fixture（input.json + expected.json）存在，含校验层预期结果、Warning 码与容差声明；引擎读取后通过 Schema 校验（test_golden.py、test_fixture_schema.py）
- [x] 边界数据覆盖：空数据、零需求、负库存、重复事件、非法单位（精度违规）、缺失批次、边界日期（tests/fixtures/edge/ 七份，test_edge_cases.py 参数化断言）
- [x] 五个 SKILL.md 骨架存在，含输入/输出 Schema 引用、错误码、降级策略；skills/manifest.json 绑定 engine 版本范围；公式 ID 与参数默认值已对齐 formula-spec（F-* 编号、ABC 80%/95%、观察窗口 90 天、服务水平 0.95）
- [x] 契约正反例测试通过：合法请求通过，必填缺失/类型错误/枚举非法/精度违规/负数量均返回 DATA_VALIDATION_FAILED 且 details 定位字段（test_analysis_contracts.py）
- [x] Hypothesis 库存守恒属性测试草案可运行（入库-出库守恒、数量非负、同输入同结果，test_conservation.py）
- [x] Ruff、mypy 在 B 侧包通过；CI workflow（engine-ci.yml）B 侧 job 就绪（GitHub Actions 生效需推送远端）
- [x] compatibility-matrix.md 初版与 CHANGELOG.md 已提交
- [x] M0 合并门槛验证记录：引擎读 fixture 过 Schema 校验 ✅、JSON Schema 与 Python 类型一致 ✅、A 调 FakeEngine 展示结果 ⏳ 待 A 联调（docs/m0-handover-b.md §4）
- [x] M0 交接说明输出（docs/m0-handover-b.md：安装命令、公开入口、依赖版本、Schema/公式版本、已知限制、回滚版本）
- [x] 对照评审报告 P0-2/P0-3 自查完成，缺口已记录为 M1 任务而非静默降级（docs/m0-handover-b.md §5：指标层黄金数值、批次 FIFO、UNIT_COST_MISSING 实现、性能阈值、wheel 交付）
