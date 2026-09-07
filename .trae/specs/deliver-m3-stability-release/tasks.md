# Tasks：deliver-m3-stability-release

执行顺序即任务编号顺序；Task 1/2 可与 Task 3 并行。所有提交在 worktree `f:\publicPro\warehouseAnalysis-b-m1` 分支 `feature/b-m3-stability-release` 上进行。

## Task 0 分支与基线同步

- [x] 在 worktree 执行 `git fetch origin && git switch -c feature/b-m3-stability-release origin/master`（worktree 当前停在旧 `feature/b-m1-kpi-engine`，需切换）。
- [x] 确认基线为 `9ef2d73`，核对 `packages/warehouse-engine/pyproject.toml` 版本漂移现状（0.2.0）仍在。
- [x] 提交 spec 三件套入库（本目录）。

## Task 1 版本对齐与守护（P0）

- [x] `packages/warehouse-engine/pyproject.toml`：version → `0.3.0`，description 更新为 M2 交付口径（五类公式 18 指标）。
- [x] `packages/contracts-python/pyproject.toml`：version → `0.3.0`（与 m2-handover-b.md §1 示例命令一致）。
- [x] 新增 `tests/engine/test_version_consistency.py`：pyproject 版本 == `ENGINE_VERSION` == 黄金数据 `engine_version` 引用 == compatibility-matrix Engine 行版本（读文件解析，防止四源漂移）。
- [x] `uv run --no-sync pytest tests/engine/test_version_consistency.py -p no:cacheprovider` 通过（5 passed）。

## Task 2 测试完备性补强（R1）

- [x] 新增 `tests/engine/test_determinism.py`：同一构造数据集连续 ≥3 次 analyze，serialize 字节级一致；digest 跨进程稳定（子进程复跑比对）。
- [x] contracts 零漂移证明：运行 `uv run python scripts/export_schemas.py`，确认 `packages/contracts-schema/` 零 diff；把复导出结论（2026-09-07、分支 `feature/b-m3-stability-release`、零 diff）记入 m3-handover-b.md §3。
- [x] 全量回归：`uv run --no-sync pytest -p no:cacheprovider` 全绿（workspace 572 passed / 19 skipped；B 侧 284 passed / 17 skipped，基线 273/17 之上新增 11 项，既有零删除零新增 skip）。
- [x] 性能复测：`uv run --no-sync python scripts/perf_bench.py` 1 万行与 10 万行档位各 ≥3 轮，全部满足 M1 阈值（analyze ≤5s / ≤40s、全链路 ≤60s）；100 万行选跑记录；数据落 m3-handover-b.md §5。

## Task 3 发布产物（R2）

- [x] 新增 `scripts/export_sbom.py`： CycloneDX JSON 片段，覆盖 warehouse-engine 0.3.0 与 contracts-python 0.3.0 及其依赖（从已解析环境/uv.lock 读取，不联网），输出 `dist/sbom-engine-0.3.0.json`；新增对应测试（结构校验：bomFormat、组件含 name/version/purl 或 hash）。
- [x] 生成依赖清单：导出锁定依赖摘要（Python 3.11、pandas/numpy/scipy/statsmodels 实际锁定版本）到 `dist/requirements-engine-0.3.0.txt`（PR 说明中注明采用文件方案）。
- [x] 构建 wheel：分两次 `uv build --package contracts-python` / `--package warehouse-engine`（`--offline` 自缓存），产出 2 wheel + 2 sdist 至 `dist/`。
- [x] 生成 `dist/SHA256SUMS`（沿用 M1 格式，覆盖 4 产物）；本地复算哈希一致。
- [x] 独立环境 wheel 冒烟：新建 venv 仅安装两个 wheel，运行黄金数据 v0.1.0 冒烟断言（`scripts/wheel_smoke.py` 随 PR 提供）。
- [x] `CHANGELOG.md`：0.3.0 从「未发布」定稿（2026-09-07），追加 M3 段落（产物、稳定性证据、staging 结论）。

## Task 4 staging 验证（R3）

- [x] 准备脱敏 staging 数据集（构造字段脱敏、保留分布特征；记录数据指纹：行数/字段/哈希）。
- [x] R3.1 版本兼容：staging venv 安装 wheel 0.3.0 + Skill manifest 校验（engine_version_range 命中）+ 黄金冒烟通过；记录兼容结论。
- [x] R3.2 回滚演练：staging venv 内 pip 降级至 engine 0.2.0（M1 wheel），验证 schema 快照仍匹配、黄金 v0.1.0/v0.2.0 通过、manifest 范围检查拒绝 0.3.0-only Skill 组合；随后升级回 0.3.0 复跑黄金全绿。
- [x] R3.3 结果可追溯：同数据集分别以 0.3.0 / 0.2.0 运行，归档两份结果（engine_version/formula_version/digest 齐全），公式口径未变则逐指标比对一致，差异逐条解释。
- [x] 全程记录 `docs/m3-staging-log-b.md`（环境、命令、哈希、异常、结论）。
- [x] A 侧端到端联调项（workbench 1.0.0 调用 engine 0.3.0）在 handover 文档标记「待 A 回填」并发起协调（m3-handover-b.md §8 / m3-staging-log-b.md §8）。

## Task 5 文档与矩阵收口（R2.6）

- [x] 新增 `docs/m3-handover-b.md`：对照需求 §9 交接包逐项索引（wheel/SHA256SUMS/schemas/golden/SKILL/CHANGELOG/compatibility-matrix/benchmark JSON），含零漂移证据、性能数据、A 侧待确认清单。
- [x] 更新 `docs/compatibility-matrix.md`：Engine 行补「wheel 已发布（M3）」、contracts 行 0.3.0、文档版本与日期、维护说明不变。

## Task 6 提交与 PR

- [x] 分批提交（版本对齐 / 测试补强 / 发布脚本与产物 / staging 与文档），信息遵循仓库惯例。
- [x] 推送 `feature/b-m3-stability-release`，按 §26.2 模板创建 PR 至 master：**PR #24** https://github.com/nihcya/warehouseAnalysis/pull/24（CI 状态见 PR 页）。
- [x] 回填 PR 链接至本文件与本 spec 状态区，勾选全部任务后请求合并评审。
