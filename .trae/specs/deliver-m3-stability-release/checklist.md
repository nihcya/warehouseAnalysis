# Checklist：deliver-m3-stability-release

评审口径：逐项核对 spec.md §5 DoD 与需求 §7 M3 / §8.3 / §9。任一失败即不通过。

## A. 范围守护

- [ ] formula_version 保持 `0.1.0`（未改任何公式口径，golden 预期值零改动）
- [ ] contracts `schema_version` 保持 `1.0`，零字段变更（export_schemas 复导出零 diff）
- [ ] 未触碰 A 侧交付物（workbench.spec / iss / control-plane / 托盘 Agent 代码零改动）
- [ ] pyproject 版本漂移已修复且仅为对齐（0.2.0 → 0.3.0，无行为变更混入）

## B. 测试完备（§7 M3 第 1 条）

- [ ] 黄金数据 v0.1.0–v0.4.0 全部通过
- [ ] 边界 fixture 全部通过（告警码为 M2 真实告警）
- [ ] 属性（Hypothesis 守恒）测试通过
- [ ] 新增重复运行确定性测试：≥3 次连续 analyze 字节级一致 + 跨进程 digest 稳定
- [ ] 新增版本一致性守护测试（pyproject / `__version__` / golden 引用 / 兼容矩阵四源一致）
- [ ] 性能复测 ≥3 轮达标：1 万行 analyze ≤5s；10 万行 analyze ≤40s、全链路 ≤60s；数据落档
- [ ] workspace 全量测试通过，既有用例零删除、零新增 skip

## C. 发布产物（§7 M3 第 2 条 / §9 交接包）

- [ ] `dist/` 含 warehouse_engine-0.3.0 与 contracts_python-0.3.0 的 wheel + sdist（4 产物）
- [ ] `dist/SHA256SUMS` 覆盖 4 产物，本地复算一致
- [ ] SBOM 片段（CycloneDX JSON）生成且结构测试通过
- [ ] 依赖清单交付（锁定版本摘要，Python 3.11）
- [ ] 独立环境仅装 wheel 跑通黄金冒烟（记录执行人/环境；沙箱受限时用户手动执行并回填）
- [ ] `CHANGELOG.md` 0.3.0 定稿 + M3 段落

## D. staging 验证（§7 M3 第 3 条）

- [ ] 脱敏数据集就绪且记录指纹（无真实客户数据）
- [ ] 版本兼容：wheel 0.3.0 + Skill manifest 范围校验 + 黄金冒烟通过
- [ ] 回滚演练：降级 0.2.0 → schema 匹配 + 黄金 v0.1.0/v0.2.0 通过 + manifest 拒绝不兼容组合 → 升回 0.3.0 全绿
- [ ] 结果可追溯：双版本结果归档（engine_version/formula_version/digest），指标一致或差异逐条解释
- [ ] `docs/m3-staging-log-b.md` 完整留档
- [ ] A 侧端到端联调项已标记「待 A 回填」并发出协调

## E. 文档与流程

- [ ] `docs/m3-handover-b.md` 对照 §9 交接包逐项可索引
- [ ] `docs/compatibility-matrix.md` Engine/contracts 行更新、版本与日期刷新
- [ ] 提交分批合理、信息清晰；PR 按 §26.2 模板；CI 绿（含 schema 漂移门禁）
- [ ] spec/tasks/checklist 勾选状态与实际一致并随 PR 入库

## F. 非目标确认（防蔓延）

- [ ] 无新增指标、无 contracts 字段新增、无 Skill 状态跃迁（保持 implemented 0.3.0）
- [ ] 100 万行性能仅为记录性选跑，未设新阈值（阈值冻结留 M3 后规划）
- [ ] 未修改 `scripts/build_release.py`（A 侧安装包链路）
