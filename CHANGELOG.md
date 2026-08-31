# 更新日志（CHANGELOG）

本文件遵循 Keep a Changelog 风格，版本号遵循语义化版本（SemVer），日期格式为 YYYY-MM-DD。
本文件是开发者 B 侧的变更记录：M0 的 contracts、engine、Skill、公式文档等组件由 B 并行交付，在此统一记录；开发者 A 侧组件（Desktop、Control API、Local DB 等）的变更由 A 侧记录。

## [0.3.0] - 2026-08-30 - 未发布（M2：分析 Skill 实现）

### Added

- 重放内核扩展（`replay.py`，向后兼容追加字段，M1 既有 120 个引擎测试无回归）：新增逐日出库序列 `daily_out_by_sku`（含零出库日补 0、当日出库+报废−退货为负时逐日取 `max(0, ·)`）、出入库日期索引 `last_outbound_date_by_sku` / `first_inbound_date_by_sku`（全重放视界，冲销不更新索引）、批次余额 `lot_balances`（FIFO 消耗、同批次再入库取最近入库日期）与全历史/残余入库加权聚合 `all_inbound_by_sku` / `residual_inbound_by_sku`（O(1) 内存换算平均库龄）；盘点后清空批次账使残余 ≠ 期末余额，由上层按「构成不足」回退。
- 四个公式实现：`calculators/abc_aging.py`（F-ABC-001/F-AGE-001/F-STALE-001）、`replenishment.py`（F-REPL-001~003）、`forecasting.py`（F-FCST-001~002）、`benchmark_compare.py`（F-BM-001）——按 `docs/formula-spec.md` 0.1.0 冻结口径实装；`WarehouseEngine.analyze` 现输出五类公式共 18 个数据集级聚合指标，移除 `ANALYSIS_PLACEHOLDER` 占位（§2.2 结果形态约定：per-SKU 明细留在计算器返回对象内，契约零改动，`schema_version` 保持 1.0）。
- 实验模型隔离（`experimental/`，§8.1 落地）：类型层（`ExperimentalForecastResult` 不可转为 `ResultMetric`、契约层即拒绝）+ 导入层（`engine` 与四个计算器均不 import 本包，默认路径不加载 `statsmodels`）+ 依赖层（`seasonal_naive` 可运行示例 / `holt_winters` 占位桩显式报未实装）；结果携带 `EXPERIMENTAL_DISCLAIMER`，不进默认 `AnalysisResult`、不参与黄金数据验收。
- 共享辅助：`calculators/_common.py` 统一舍入（金额 HALF_UP 0.01、比率 6 位）、null 表示、parameters 容错读取与指标构造，避免四个新增计算器各写一份而漂移。
- 黄金数据：新增 `tests/fixtures/golden/v0.3.0/`（ABC/库龄/呆滞焦点：累计占比 80/95 边界、五档库龄、新品豁免优先于天数判定、呆滞率 0.25）与 `v0.4.0/`（补货/预测/基准焦点：σ_d=0 边界、非恒定 σ_d≈15.257924、基准相对偏差 −0.173913）；回填 `v0.1.0`/`v0.2.0` 结构性可推导项（F-ABC-001 参与分类 SKU 数、F-REPL-001~003 缺参数降级、F-BM-001 null+`benchmark_unavailable`），全部手算推导、禁止引擎回填。
- 版本化基准 fixture：`tests/fixtures/benchmarks/v0.1.0.json`（八字段合规 + 不合规记录丢弃用例），经 `request.parameters["benchmarks"]` 注入（引擎不读文件、不访问网络）。

### Changed

- 版本提升：warehouse-engine 0.2.0 → 0.3.0（formula_version 保持 0.1.0，口径未变，仅实现扩展，历史结果无需重算）；kpi Skill 兼容范围放宽为 `>=0.2.0,<0.4.0`，四个 Skill 0.1.0 → 0.3.0 转 implemented（engine `>=0.3.0,<0.4.0`）。
- 重放内核 `apply()` 改为返回对 `period_out_qty` 的增量（已按 `base_in_period` 门控），`apply_and_track` 复用该增量累计逐日出库，保证 `Σ daily_out == period_out_qty` 恒成立。
- `inventory_kpi.calculate` 新增可选 `outcome` 入参（不传时自行重放），`analyze` 现只重放一次并将结果注入四个计算器。
- 边界 fixture（5 份）analyze_warnings 由 `ANALYSIS_PLACEHOLDER` 更新为 M2 真实告警（NO_OUTFLOW/PARAM_MISSING/INSUFFICIENT_SAMPLES/BENCHMARK_UNAVAILABLE），逐条手验触发条件。

### 说明

- M2 实现决策（待 A/B 复核，完整记录在 `docs/m2-handover-b.md`）：§8.3 的「有效需求期数 n < 12」按全期间自然周数理解（非预测窗口），否则缺省 split_date 下会恒定触发降级；`ReplenishmentRecord.avg_daily_demand` 与 §7.2 的 d̄ 口径不同源，本计算器不消费该字段；BM.DEVIATION_RATIO 跨单位取相对偏差均值（不求和）。
- wheel 不在 M2 产出（留 M3 发布门槛）；如需本地构建与独立环境验证见 `docs/m2-handover-b.md` §1。
- 兼容性要求详见 `docs/compatibility-matrix.md`。

## [A 侧 0.2.0] - 2026-08-30 - 未发布（M2：控制平面认证授权与商户端状态）

> A 侧控制平面（services/control-plane）变更。分支 `feature/a-m2-control-plane-auth`；版本 0.1.0 → 0.2.0；云端迁移 `control-0001` → `control-0004`。

### Added

- 认证闭环：`POST /auth/login`、`POST /auth/refresh`、`POST /auth/logout`、`GET /account/me`；密码 **Argon2id** 哈希、Access Token 为 **JWT（15min）**、Refresh Token 一次性轮换且**仅存 SHA-256 指纹**。
- 刷新令牌重放检测：轮换时旧指纹移入 `previous_refresh_token_hash`，已失效令牌再次使用即判定重放并吊销该账号全部会话（401 `AUTH_REQUIRED`）。
- 设备注册：`POST /devices/register`（同 `(tenant_id, fingerprint)` 幂等、达 `max_devices` 拒绝、已吊销指纹拒绝）、`GET /devices`（租户隔离）。
- 许可证与离线宽限期：状态机 `ACTIVE`/`GRACE`/`EXPIRED`/`REVOKED`；宽限期默认 **7 天**（`LICENSE_OFFLINE_GRACE_DAYS` 可配），超期返回 403 `LICENSE_EXPIRED`。
- Scope 与租户隔离：`require_tenant_access` / `require_developer_scope` / `require_tenant_with_license` 校验真实 JWT；**dev token（`Bearer merchant`/`developer`）下线**。
- 审计：`audit_log` 只追加，登录成功/失败、刷新、注销、设备注册、许可证变更全部留痕，`detail_json` 白名单过滤、不含敏感原文。
- 实时状态：`GET /events/stream`（SSE，单调事件 ID + `Last-Event-ID` 续传 + 15s keepalive）与 `GET /events/snapshot?since=`（30s 轮询降级）；`EventHub` 环形缓冲。
- 仓储双实现：定义 `application/ports.py` 协议，**内存实现**（测试注入 / 本地演示，生产拒绝）+ **PostgreSQL 实现**（生产路径），组合根 `container.py` 按 `CONTROL_PLANE_REPOSITORY` 选择。
- 云端迁移：`control_0002`（tenant/account/session/device）、`control_0003`（product_profile/license/feature_grant）、`control_0004`（audit_log，自 §35.7 的 0006 提前）。
- Web 商户端：`apps/web` 登录页接真实 API、商户工作台展示许可证/设备/版本状态、状态流 hook（SSE + 轮询降级）；`package.json` 加 `@ant-design/icons`。
- 文档：`docs/data-dictionary.md` §2.3~2.10、`docs/er-diagram.md` §2.2~2.3、`docs/m2-handover-a.md`、`DECISIONS.md` D-013~D-020。

### Changed

- 统一成功响应包 `{"data": ...}`；错误包沿用 `contracts.enums.ErrorCode`（零新增枚举）。
- `/heartbeat`、`/tasks*`、`/sync/*`、`/config`、`/telemetry`、`/merchants` 维持 stub 501，但鉴权依赖从 dev token 改为真实 JWT。
- `control_meta` 种子 `db_schema_version` 升 `control-0004`、`control_plane_version` 升 `0.2.0`；枚举种子扩至 67 行。

## [0.2.0] - 2026-08-29 - 未发布（M1：KPI/COGS 引擎实现）

### Added

- 重放内核：`packages/warehouse-engine/src/warehouse_engine/replay.py`——事件按 `(move_date, occurred_at, event_id)` 升序重放、期间归属 `[start_date, end_date)`、冲销引用链解析（撤销的撤销 = 复原）、盘点余额替换、调拨 no-op、负余额跟踪（`NEGATIVE_BALANCE`）与移动加权平均成本状态。
- 九个公式实现：`calculators/inventory_kpi.py` 落地 F-KPI-001~008（期初/期末库存、出库量、动销率、库存价值、周转率、周转天数、覆盖天数）与 F-COGS-001（COGS，移动加权平均）；`WarehouseEngine.analyze` 由占位返回升级为真实计算路径（先校验后计算；abc-aging/replenishment/forecasting/benchmark 四类维持 M0 占位并以 `ANALYSIS_PLACEHOLDER` 标注）。
- 数据质量报告：contracts 新增 `DataQualityEntry`；`AnalysisResult.data_quality` 按 Warning 码汇总（计数 + 明细，条目按 code 字典序，确定性）随结果返回。
- contracts 新增可选字段（非破坏，schema_version 保持 1.0）：`MovementRecord.reversal_of`（冲销引用）、`ResultMetric.reason`（null/降级原因标注）、`AnalysisResult.data_quality`（数据质量报告）。
- 黄金数据指标层：`tests/fixtures/golden/v0.1.0/` 由 M0 校验层扩展至指标数值层（含推导说明与容差声明）；新增 `tests/fixtures/golden/v0.2.0/`（历史盘点/冲销期初、期末 0 与负余额、退货冲减为负、UNIT_COST_MISSING 价值回退、期内冲销等边界）。
- 性能基线（`scripts/perf_bench.py`，seed=20260829）：1 万行 analyze 1.49/1.80 秒；10 万行 analyze 15.80/12.38 秒、全链路（build+validate+analyze+digest）约 36 秒；100 万行 analyze 171.25 秒（仅 1 次实测，阈值 M3 冻结）。M1 阈值：1 万行 analyze ≤5 秒；10 万行 analyze ≤40 秒、全链路 ≤60 秒。
- wheel 首次交付：`dist/` 本地构建 `warehouse_engine-0.2.0` 与 `contracts_python-0.2.0` 两个 wheel + sdist（共 4 个产物）及 `dist/SHA256SUMS`；两 wheel 一起分发，独立环境安装验证通过。

### Changed

- 版本提升：warehouse-engine 0.1.0 → 0.2.0、contracts-python 0.1.0 → 0.2.0；`formula_version` 保持 0.1.0（口径已冻结，M1 未变更任何规则）。
- 校验增强：冲销引用缺失/成环与盘点实盘数量非正新增为阻断校验（`DATA_VALIDATION_FAILED`）。
- Skill 对齐：`skills/kpi/SKILL.md` 升至 0.2.0（implemented），公式步骤从占位改为 engine 0.2.0 实现引用；`skills/manifest.json` kpi engine 范围改为 `>=0.2.0,<0.3.0`，四个占位 Skill 放宽为 `>=0.1.0,<0.3.0`；`docs/compatibility-matrix.md` 同步 Engine 0.2.0 与 Skill bundle 0.2.0。

## [0.1.0] - 2026-08-29 - 未发布（M0 骨架）

### Added

- `packages/contracts-python` 0.1.0：`enums.py`（`MoveType`、`EventSource`、B 侧最小错误码、`WarningSeverity`）与 `analysis.py`（`AnalysisRequest`、`EngineDataset`、`AnalysisResult`、`ValidationReport`、`Warning`）。
- `packages/contracts-schema`：引擎侧 JSON Schema `analysis-request.schema.json` 与 `analysis-result.schema.json`（contracts 1.0）。
- `packages/warehouse-engine` 0.1.0 骨架：`engine.py`、`contracts.py`、`result.py`、`errors.py`、`validation/` 与 `calculators/`（五个计算器空壳）。
- 五个 Skill 骨架：`skills/{kpi,abc-aging,replenishment,forecasting,benchmark}/SKILL.md`（状态 skeleton，绑定 engine `>=0.1.0,<0.2.0`）。
- `skills/manifest.json`：Skill 清单（`manifest_version` 1.0）。
- `docs/compatibility-matrix.md`：兼容矩阵初版（Desktop / Control API / Engine / Skill bundle / contracts / Local DB schema）。
- `docs/formula-spec.md` 0.1.0 草案：KPI/COGS、ABC/库龄/呆滞、补货、预测与误差公式口径冻结文档（含 `formula_version` 标识）。

### 说明

- 本版本为 M0 骨架，公式未冻结，未打 Git Tag、未产出 wheel，不作为生产版本分发。
- FakeEngine、黄金数据、边界数据、契约测试与 CI 入口由 B 侧并行任务交付，完成后在本文件补充记录。
- 兼容性要求详见 `docs/compatibility-matrix.md`。
