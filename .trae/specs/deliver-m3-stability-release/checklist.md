# Checklist：deliver-m3-stability-release

评审口径：逐项核对 spec.md §5 DoD 与需求 §7 M3 / §8.3 / §9。任一失败即不通过。

## A. 范围守护

- [x] formula_version 保持 `0.1.0`（未改任何公式口径，golden 预期值零改动）
- [x] contracts `schema_version` 保持 `1.0`，零字段变更（export_schemas 复导出零 diff，2026-09-07 复测）
- [x] 未触碰 A 侧交付物（workbench.spec / iss / control-plane / 托盘 Agent 代码零改动）
- [x] pyproject 版本漂移已修复且仅为对齐（0.2.0 → 0.3.0，无行为变更混入）

## B. 测试完备（§7 M3 第 1 条）

- [x] 黄金数据 v0.1.0–v0.4.0 全部通过
- [x] 边界 fixture 全部通过（告警码为 M2 真实告警）
- [x] 属性（Hypothesis 守恒）测试通过
- [x] 新增重复运行确定性测试：≥3 次连续 analyze 字节级一致 + 跨进程 digest 稳定（test_determinism.py）
- [x] 新增版本一致性守护测试（pyproject / `__version__` / golden 引用 / 兼容矩阵四源一致，5 passed）
- [x] 性能复测 ≥3 轮达标：1 万行 analyze ≤5s；10 万行 analyze ≤40s、全链路 ≤60s；数据落档（m3-handover-b.md §5）
- [x] workspace 全量测试通过（572 passed / 19 skipped；B 侧 284/17），既有用例零删除、零新增 skip

## C. 发布产物（§7 M3 第 2 条 / §9 交接包）

- [x] `dist/` 含 warehouse_engine-0.3.0 与 contracts_python-0.3.0 的 wheel + sdist（4 产物）
- [x] `dist/SHA256SUMS` 覆盖 4 产物，本地复算一致
- [x] SBOM 片段（CycloneDX 1.5 JSON）生成且结构测试通过（test_sbom_export.py）
- [x] 依赖清单交付（dist/requirements-engine-0.3.0.txt，Python 3.11.15 锁定版本）
- [x] 独立环境仅装 wheel 跑通黄金冒烟（scripts/wheel_smoke.py，全新 venv，18 指标 + digest 断言）
- [x] `CHANGELOG.md` 0.3.0 定稿（2026-09-07）+ M3 段落

## D. staging 验证（§7 M3 第 3 条）

- [x] 脱敏数据集就绪且记录指纹（全合成种子 20260906，sha256 3e66f5c6…，无真实客户数据）
- [x] 版本兼容：wheel 0.3.0 + Skill manifest 范围校验（5 Skill 全命中）+ 黄金冒烟通过
- [x] 回滚演练：降级 0.2.0 → schema 零 diff + 黄金 v0.1.0/v0.2.0 通过 + manifest 拒绝 4 个 0.3.0-only Skill → 升回 0.3.0 结果字节级复现
- [x] 结果可追溯：双版本结果归档（engine_version/formula_version/digest 齐全），9 个共享指标值一致，差异逐条解释
- [x] `docs/m3-staging-log-b.md` 完整留档（环境/指纹/命令/哈希/结论）
- [x] A 侧端到端联调项已标记「待 A 回填」（m3-handover-b.md §8）并发出协调

## E. 文档与流程

- [x] `docs/m3-handover-b.md` 对照 §9 交接包逐项可索引（§7 表格）
- [x] `docs/compatibility-matrix.md` Engine/contracts 行更新、版本与日期刷新（文档版本 0.4.0）
- [ ] 提交分批合理、信息清晰；PR 按 §26.2 模板；CI 绿（含 schema 漂移门禁）——提交与推送已完成，PR 待创建（gh 认证失效）
- [x] spec/tasks/checklist 勾选状态与实际一致并随 PR 入库

## F. 非目标确认（防蔓延）

- [x] 无新增指标、无 contracts 字段新增、无 Skill 状态跃迁（保持 implemented 0.3.0）
- [x] 100 万行性能仅为记录性选跑，未设新阈值（阈值冻结留 M3 后规划）
- [x] 未修改 `scripts/build_release.py`（A 侧安装包链路）
