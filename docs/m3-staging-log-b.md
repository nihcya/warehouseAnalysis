# M3 staging 验证留档（开发者 B）

**执行日期**：2026-09-06 17:57–17:58（一次通过，无重试）  
**归档日期**：2026-09-07  
**执行人**：开发者 B  
**对应需求**：`开发需求-B引擎Skill.md` §7 M3 第 3 条（staging：版本兼容 / 回滚 / 结果可追溯）  
**验证脚本**：`scripts/staging_verify.py`（随本 PR 入库，`uv run python scripts/staging_verify.py` 可重复执行，固定种子数据集可复现）  
**结论**：**PASS**——三项验证（兼容 / 回滚 / 可追溯）全部通过

---

## 1. 环境

| 项 | 值 |
|---|---|
| 操作系统 | Windows（本机，非 CI 环境） |
| Python | 3.11（`uv venv --python 3.11`） |
| 包管理 | uv（`uv pip install --offline`，自 uv 本地缓存解析，不联网） |
| staging venv | 独立目录 `%TEMP%\wh-staging-m3\venv`（**非** workspace `.venv`），全程仅安装 wheel |
| 0.3.0 wheel | `dist/warehouse_engine-0.3.0-py3-none-any.whl` + `dist/contracts_python-0.3.0-py3-none-any.whl`（本 PR 构建，SHA-256 见 `dist/SHA256SUMS`） |
| 0.2.0 wheel | M1 构建产物（`warehouseAnalysis-b-m1/dist/`，回滚演练用） |

## 2. 脱敏数据集（R3 前置）

**全合成数据**（`scripts/staging_verify.py::build_staging_payload`，固定种子 20260906），无任何真实客户数据。分布特征：2 仓库 × 4 品类 × 12 SKU、两批入库不同成本、工作日加权出库、偶发退货、期末盘点；10/12 SKU 有补货参数（2 缺 → `PARAM_MISSING`）、基准仅覆盖 2 品类（另 2 品类 → `BENCHMARK_UNAVAILABLE`）。

指纹（`staging-fingerprint.json`）：

| 项 | 值 |
|---|---|
| run_id | `run-staging-m3-0001` |
| 期间 | 2026-06-01 ~ 2026-07-31（warehouse WH-01 / WH-02） |
| 规模 | 12 SKU / 834 movements / 24 snapshots / 10 replenishment |
| payload SHA-256 | `3e66f5c649ea31a7d248ec4548cc1b2f2739c094c402e70e372230be29b8f563` |

## 3. R3.1 版本兼容（engine 0.3.0）

步骤与结果（逐条对应脚本输出）：

1. staging venv 安装 0.3.0 双 wheel（`uv pip install --offline`）；
2. Skill manifest 范围校验：engine `0.3.0` 命中全部 5 个 Skill（kpi / abc-aging / replenishment / forecasting / benchmark），拒绝无；
3. 黄金数据 v0.1.0 完整冒烟（`scripts/wheel_smoke.py --smoke-only`，仅依赖已装 wheel）：**通过**，engine 0.3.0 / formula 0.1.0 / 18 指标 / digest `dd3200c337e1e5b3…`；
4. staging 数据集分析结果归档：18 指标，engine_version 0.3.0 / formula_version 0.1.0 / dataset_digest `0b2e06e07781cc34…` → `result-engine-0.3.0.json`。

## 4. R3.2 回滚演练（0.3.0 → 0.2.0 → 0.3.0）

**降级 0.2.0**：

1. 同一 staging venv `uv pip install` 0.2.0 双 wheel（覆盖降级，模拟生产回滚）；
2. manifest 范围校验：engine `0.2.0` 仅命中 `kpi`，拒绝 `abc-aging` / `replenishment` / `forecasting` / `benchmark`（0.3.0-only Skill 组合的不兼容守护生效）；
3. 已装 contracts 0.2.0 导出的两份 JSON Schema 与仓库快照 `packages/contracts-schema/` **零 diff**（契约跨版本稳定）；
4. 黄金数据 v0.1.0 / v0.2.0 冒烟（0.2.0 子模式）：各 **9 项 KPI/COGS 冻结值按 §10 容差全部一致**，且保留 `ANALYSIS_PLACEHOLDER` 告警（0.2.0 行为特征）；
5. staging 数据集 0.2.0 结果归档：9 指标 / formula 0.1.0 / digest `0b2e06e07781cc34…` → `result-engine-0.2.0.json`。

**升回 0.3.0 复验**：

6. 重装 0.3.0 双 wheel，黄金冒烟再次通过；staging 数据集复跑结果与首次 0.3.0 运行**逐字节一致**（SHA-256 相同，见 §6）。

## 5. R3.3 结果可追溯（双版本对比）

同一 staging 数据集（dataset_digest `0b2e06e07781cc34…` 双版本一致）：

- **9 个共享 KPI/COGS 指标值完全一致**（F-KPI-001~008 / F-COGS-001，formula_version 0.1.0 未变，历史报告可凭版本号 + digest 复现）；
- 差异逐条（引擎行为差异，非口径变更）：
  - 0.3.0 新增 9 项指标：F-ABC-001 / F-AGE-001 / F-STALE-001 / F-REPL-001~003 / F-FCST-001~002 / F-BM-001；
  - 0.2.0 告警含 `ANALYSIS_PLACEHOLDER` 1 条；0.3.0 为真实告警 `INSUFFICIENT_SAMPLES` / `PARAM_MISSING` / `PERIOD_MISMATCH`。

## 6. 归档产物与哈希（SHA-256）

| 文件 | SHA-256 | 说明 |
|---|---|---|
| staging-dataset.json | `3e66f5c6…29b8f563`（payload） | 脱敏数据集（固定种子可复现） |
| result-engine-0.3.0.json | `b2725a3b7684210b92505452670f13644d7ca18bafdb690ecc214cc9b94efd16` | 0.3.0 首次运行结果 |
| result-engine-0.3.0-replay.json | 同上（逐字节一致） | 升回 0.3.0 后复跑（复现证明） |
| result-engine-0.2.0.json | `97f264434ae14d8b2ce3424603e59b3a3d86de53dd26f282e32e2d7e5decb0ff` | 回滚 0.2.0 运行结果 |

产物目录为 `%TEMP%\wh-staging-m3\`（本机留档）；复验方式：重跑 `scripts/staging_verify.py`，固定种子保证数据集与结果确定性重现。

## 7. 异常与处理

无。一次运行全部通过，无人工干预、无重试。

## 8. A 侧回填（已回填，2026-09-07）

workbench 1.0.0 调用 engine 0.3.0 的**端到端联调**不在 B 侧可自证范围，由 A 侧于
2026-09-07 完成回填验证（`docs/m3-handover-b.md` §8 对应项据此关闭）。

**验证方式**：以 B 侧同一确定性 staging 数据集（`scripts/staging_verify.py::
build_staging_payload`，固定种子）为输入，走 **A 侧工作台引擎接入路径**
（`LocalEngineProvider`，即工作台组合根注入的真实引擎提供方，
`WORKBENCH_ENGINE=local` 缺省路径）完成 validate → analyze 全链路。

**结果（三项全部一致）**：

| 项 | 值 | 与 B 侧一致 |
|---|---|---|
| 数据集指纹（payload SHA-256） | `3e66f5c6…29b8f563` | ✅ |
| 引擎 / 公式版本 | `0.3.0` / `0.1.0` | ✅ |
| 分析结果（18 指标，CRLF 口径 SHA-256） | `b2725a3b…b94efd16` | ✅ 与 `result-engine-0.3.0.json` 逐字节一致 |

其他佐证：`validate_dataset` 通过（0 issues）；18 指标公式 ID 齐备
（F-KPI-001~008 / F-COGS-001 / F-ABC-001 / F-AGE-001 / F-STALE-001 /
F-REPL-001~003 / F-FCST-001~002 / F-BM-001）；
`dataset_digest` 与 B 侧归档一致（同一数据集）。

> 备注：逐字节比对时注意归档文件经 `Path.write_text` 在 Windows 落盘为
> **CRLF** 行尾；内存序列化为 LF。按 CRLF 口径哈希方与归档一致
> （LF 口径为 `7c81f77a…`，内容逐行 diff 为空）。

**结论**：A 侧工作台调用 engine 0.3.0 的分析链路与 B 侧 staging 归档
**结果完全一致**，staging R3.1 的 A 侧半边验证通过。
