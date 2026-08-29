# 开发者 A：M0 交接说明（G2 前置验收材料）

**日期**：2026-08-29  
**交付人**：开发者 A（平台集成：control-plane、local-data、workbench-desktop、apps/web、契约文件）  
**对应里程碑**：M0 契约与基线（`开发规划与协作需求文档.md` V2.0 §22.2、`开发需求-A平台工作台.md` §4 M0）  
**代码分支**：`feature/a-m0-platform-baseline`（自 `master` 41f135d 切出，B 基线 47 测试起点全绿）  
**版本基线**：control-plane 0.1.0 / 本地库 schema `local-0002` / 云端库 schema `control-0001` / web 0.1.0 / contracts 1.0 / engine 0.1.0 / formula 0.1.0-draft

---

## 1. 安装与启动命令

```powershell
# 环境安装（uv 自动解析 CPython 3.11.15；Web 另需 Node.js 22 LTS + pnpm）
uv sync --all-packages --group dev
pnpm install

# 本地 PostgreSQL（云端控制库迁移与 /health 数据库探测用；无 PG 时迁移测试自动 skip）
docker compose -f docker-compose.dev.yml up -d postgres

# 启动云端控制平面（FastAPI，http://localhost:8000，交互文档 /docs）
#   注意：须在服务目录运行（uv run --package 不切换 cwd，仓库根执行会报 ModuleNotFoundError: app）
cd services/control-plane; uv run uvicorn app.main:app --reload --port 8000

# 启动桌面工作台（PySide6；默认 WORKBENCH_ENGINE=fake 读冻结 fixture）
#   同理须在应用目录运行；环境变量见 .env.example：WORKBENCH_ENGINE=fake|local、
#   WORKBENCH_DATA_DIR、WORKBENCH_FAKE_FIXTURE
cd apps/workbench-desktop; uv run python -m app

# 启动 Web（Next.js，http://localhost:3000）
pnpm --filter web dev

# 全量检查
uv run pytest                                   # 114 passed, 1 skipped（见 §3）
uv run ruff check packages scripts tests services local-data apps
pnpm --filter web lint
pnpm --filter web typecheck
pnpm --filter web build

# OpenAPI 快照重导出（漂移由 test_openapi_sync.py 一致性测试看护）
uv run python scripts/export_openapi.py

# 迁移结果结构校验（本交接 §3 与 er-diagram.md §3 的机器证据）
uv run python scripts/verify_schema.py
```

工作台 UI 测试与联调在无头环境执行：`QT_QPA_PLATFORM=offscreen`（`apps/workbench-desktop/tests/conftest.py` 已默认设置）。

## 2. 公开入口清单

### 2.1 控制平面（services/control-plane，端口 8000）

`GET /health` 返回 200：`{status, app_version, database}`（数据库不可达仍 200、database=down 如实上报）。

`/api/v1` 下 13 个 stub 路由（业务逻辑未实现，统一 501 + `{"error": {code, message, details, request_id}}`，code 取 `contracts.enums.ErrorCode`）：

| 方法 | 路径 | Scope 依赖 | 说明 |
|---|---|---|---|
| POST | `/api/v1/auth/login` | 无 | 账号登录（真实认证 M2） |
| POST | `/api/v1/auth/refresh` | 无 | 刷新访问令牌 |
| POST | `/api/v1/auth/logout` | 无 | 退出登录 |
| POST | `/api/v1/devices/register` | 无（首次激活） | 设备注册 |
| GET | `/api/v1/devices` | `require_tenant_access` | 商户设备列表 |
| GET | `/api/v1/config` | `require_tenant_access` | 拉取商户生效配置 |
| GET | `/api/v1/tasks` | `require_tenant_access` | 商户调度任务列表 |
| POST | `/api/v1/tasks/pull` | `require_tenant_access` | 设备拉取待执行任务 |
| GET | `/api/v1/sync/events/pull` | `require_tenant_access` | 拉取加密同步事件信封 |
| POST | `/api/v1/sync/ack` | `require_tenant_access` | 确认同步事件已应用 |
| GET | `/api/v1/telemetry` | `require_developer_scope` | 开发者查询技术日志 |
| GET | `/api/v1/merchants` | `require_developer_scope` | 开发者查询商户列表 |
| POST | `/api/v1/heartbeat` | `require_tenant_access` | 设备心跳上报 |

受保护端点的负向行为（测试覆盖，`test_api_stubs.py` 29 例）：无 Authorization 凭证 → 401 `AUTH_REQUIRED`；商户 Scope 访问开发者接口 → 403 `AUTH_FORBIDDEN`。OpenAPI 快照提交于 `packages/contracts-schema/openapi.json`（导出脚本 `scripts/export_openapi.py`；重导出一致性测试 `services/control-plane/tests/test_openapi_sync.py`）。

### 2.2 Web（apps/web，端口 3000）

| 路由 | 路由组 | 说明 |
|---|---|---|
| `/` | `(public)` | 官网首页占位 |
| `/login` | `(public)` | 登录页占位（M0 无真实登录，提交不触达后端） |
| `/dashboard` | `(merchant)` | 商户工作台占位 |
| `/developers` | `(developer)` | 开发者管理模式占位 |

前端请求 DTO 一律由 `openapi-typescript` 从 `openapi.json` 生成（`pnpm --filter web generate:api` → `src/api/types/schema.d.ts`），禁止手写。

### 2.3 工作台与本地库（A 侧其余入口）

| 入口 | 位置 | 说明 |
|---|---|---|
| `WarehouseWorkbench` 主窗口 | `apps/workbench-desktop/app/main.py` | 导航（总览/货品/库存流水/分析/调度/报告/备份/设置）+ 状态栏（网络/授权/待同步/版本，M0 离线占位） |
| `EngineProvider` Protocol | `app/domain/engine_provider.py` | `WORKBENCH_ENGINE=fake\|local` 仅组合根读取，UI 无 `if fake` 分支 |
| 本地库连接工厂 | `local_data.connection` | 数据目录解析 + PRAGMA（foreign_keys/WAL/busy_timeout） |
| Repository | `local_data.repository` / `workbench.infrastructure.db.result_store` | 结果按 run_id 原样存取 |

## 3. 验证记录（2026-08-29，分支 `feature/a-m0-platform-baseline`）

环境：Windows + CPython 3.11.15（uv.lock 锁定）+ Node 22 LTS。

| 检查 | 命令 | 结果 |
|---|---|---|
| 全量测试 | `uv run pytest` | **114 passed, 1 skipped**（本地无 PostgreSQL，skip = 云端迁移 roundtrip `services/control-plane/tests/test_migrations.py`，CI 由 PG service 容器执行；B 基线 47 例无回归） |
| Python 静态检查 | `uv run ruff check packages scripts tests services local-data apps` | All checks passed |
| Web lint | `pnpm --filter web lint` | 通过（eslint 退出码 0，无错误/告警输出） |
| Web 类型检查 | `pnpm --filter web typecheck` | 通过（tsc --noEmit） |
| Web 构建 | `pnpm --filter web build` | 通过：7 个静态页生成，业务路由 4 条（/、/login、/dashboard、/developers，全部 prerendered） |
| Schema 结构校验 | `uv run python scripts/verify_schema.py` | 本地 SQLite 全项通过（13 项检查：表/alembic_version/种子/UNIQUE/索引/外键/主键及约束实际生效）；云端 PostgreSQL skip（本地无 PG，CI 由 service 容器执行真实校验） |
| 控制平面冒烟 | `cd services/control-plane; uv run uvicorn app.main:app` → `GET /health` | 200：`{"status":"ok","app_version":"0.1.0","database":"down"}`（无 PG 时 database 如实为 down，不伪报） |

测试分布（`pytest --collect-only`：115 例）：B 基线 47（engine 30 + contract 17）+ A 新增 68（local-data 15、control-plane 34、workbench-desktop 9、sync-envelope 契约 10）。

## 4. 协作证据（关闭 `docs/m0-handover-b.md` §8 三项待办）

### 4.1 Task 1.5 —— A 提交 sync-envelope 与 openapi.json，B 参与评审

A 侧已交付五份公共契约文件中 A 主导的两份：

- `packages/contracts-schema/sync-envelope.schema.json`：9 字段全必填（event_id/merchant_id/target_device_id/idempotency_key/algorithm/nonce/ciphertext/created_at/expires_at）、`algorithm` 冻结 `AES-256-GCM`、`additionalProperties: false`、前缀 pattern 与幂等键长度约束齐备；`created_at < expires_at` 为应用层校验并在测试覆盖。正反例测试 `tests/contract/test_sync_envelope.py`（10 例：合法样例通过；缺字段、非法 algorithm、过期时间早于创建时间、前缀/长度非法等失败并定位字段）。
- `packages/contracts-schema/openapi.json`：FastAPI 应用导出快照（`scripts/export_openapi.py` + `test_openapi_sync.py` 防漂移；web 类型由 openapi-typescript 消费，Task 8.3）。

**状态：A 侧交付完成；待 B 评审回签（B 侧 m0-handover-b.md §5 P0-3 中的待办项）。**

### 4.2 Task 2.5 —— formula-spec.md 走查（草案转冻结的 A 侧签字）

A 走查 `docs/formula-spec.md`（0.1.0-draft，formula_version 0.1.0）结论：**同意转冻结**。依据：

- 七类口径（KPI/COGS、ABC、库龄、呆滞、补货、预测/误差、基准）均有公式 ID（F-*）、版本、状态、输入字段、边界规则、Warning 码与黄金数据要求；
- 评审报告指出的五类口径歧义（期初库存、COGS、库龄、ABC、MAPE）均有明确裁定：期间左闭右开 + `move_date` 归属、COGS 冻结移动加权平均（FIFO 列为后续版本）、库龄观察点取期末余额、ABC 按 80%/95% 累计金额分层、预测误差含样本不足降级（<12 期）与容差声明（§10 黄金数据与容差总表，abs/rel 双通道）；
- §11 变更规则满足"只加不改 + 提版本 + 黄金数据并行一个周期 + 旧报告不追溯重算"的版本治理要求。

**状态：A 侧走查通过并同意冻结；本节即走查记录，formula-spec.md 会签表落笔随本 PR 合并时由 A/B 各自补签。**

### 4.3 Task 7.1 剩余 —— 工作台以 FakeEngine 实际接入并展示结果

工作台全链路已跑通（validate → analyze → 展示 → 持久化 → 重查），证据为可复现命令与测试：

- 命令：`QT_QPA_PLATFORM=offscreen uv run pytest apps/workbench-desktop -q`（并入全量 `uv run pytest`，conftest 已默认 offscreen）；图形界面手动运行：`cd apps/workbench-desktop; uv run python -m app`。
- `test_main_window.py::test_analysis_page_full_chain_display_and_persistence`：点击运行 → 进度条由 progress 回调驱动至 100% → 结果表格一行五列（run_id、engine_version=0.1.0-fake、formula_version=0.1.0、metrics 含 KPI.OUTBOUND_QTY、warnings 含 ANALYSIS_PLACEHOLDER）→ 落库 → 清空后按 run_id 重查内容一致；
- `test_analysis_page_validation_failure_shows_issues`：阻断校验（DUPLICATE_EVENT）时展示错误列表、进度不动、不落库、不调用 analyze；
- `test_analysis_usecase.py`（5 例）：golden 输入构造契约模型、结果按 run_id 存取往返一致、组合根 `WORKBENCH_ENGINE` 分支只在 `app.main` 出现。

**状态：B 侧 m0-handover-b.md §4 第 3 门槛（"A 的工作台可调用 FakeEngine 并展示结果"）从"待 A 实际接入"转为已验证；联调证据即本节。**

## 5. 对 B 三份契约文件的评审结论（关闭 Task 6.4 / B §8 Task 1.5 的 A 侧评审）

评审对象：`packages/contracts-python/src/contracts/enums.py`、`packages/contracts-python/src/contracts/analysis.py`、`packages/contracts-schema/analysis-request.schema.json`、`packages/contracts-schema/analysis-result.schema.json`。

**结论：通过，M0 契约冻结确认（结构完整、错误码集合满足 A 侧 API 需求、Decimal 字符串序列化约定明确）。**

1. **结构完整**：`AnalysisRequest` / `EngineDataset`（SkuRecord/MovementRecord/SnapshotRecord/ReplenishmentRecord）/ `AnalysisResult`（metrics/warnings/summary/input_summary）与 A 侧落库字段一一对应——`run_id`、`engine_version`、`formula_version` 可直接写入 `analysis_run`，`AnalysisResult` 整体 JSON 原样入 `analysis_result.metric_json`；`Warning` 五要素（code/severity/message/fields/blocking）满足 UI 按 code 判断行为的需求；`ValidationIssue.field` 能定位到具体字段（如 `movements.quantity`），满足工作台校验失败列表展示（Task 7.5）。
2. **错误码集合满足 A 侧 API 需求**：`AUTH_REQUIRED`/`AUTH_FORBIDDEN`（401/403 依赖占位）、`INTERNAL_ERROR`（501 stub 统一码）、`LICENSE_EXPIRED`、`DEVICE_REVOKED`、`CONFIG_VERSION_UNSUPPORTED`/`CONFIG_SIGNATURE_INVALID`、`SYNC_ENVELOPE_EXPIRED`/`SYNC_CURSOR_INVALID`、`DATA_VALIDATION_FAILED` 等控制平面所需错误码齐备，`app/api/v1/errors.py` 全部取自该枚举，无缺口；`MoveType`/状态枚举与云端 `control_enum` 种子同源（迁移直接 import `contracts.MoveType`）。
3. **Decimal 字符串序列化约定明确**：`DecimalAmount = Annotated[Decimal, PlainSerializer(...→str)] + WithJsonSchema(type=string)`，JSON 传输与本地库 TEXT 存储同口径，杜绝 float 真值泄漏；日期 `YYYY-MM-DD`、时间 UTC ISO 8601 与 A 侧本地库存储约定一致。
4. **两份 JSON Schema 与 Python 类型一致性**：由 `tests/contract/test_schema_sync.py` + `engine-ci.yml` 重导出 `git diff --exit-code` 门禁看护；A 侧 `openapi.json` 采用同口径管理（`test_openapi_sync.py`），五份公共文件齐备。

小建议（不阻塞 M0 冻结，供 B 在 M1 参考）：

1. `Warning.code` 为自由字符串（PERIOD_MISMATCH、ANALYSIS_PLACEHOLDER 等不在 `ErrorCode` 枚举中），而 `ValidationIssue.code` 是 `ErrorCode` 枚举——两个 code 域并存。M1 若 UI 需按 code 做国际化或行为分支，可考虑为 Warning 码建立受控词表/注册表。
2. `ResultMetric.value` 为 `float | int | str` 联合类型：M0 数量少无碍，M1 指标增多后建议在 formula-spec 的指标定义中标注每个指标的值类型，方便表格渲染分支。
3. `AnalysisResult.summary` 为自由文本：M1 报告页若需结构化展示（分节摘要），建议以可选字段"只加不改"地补充结构化摘要。

**签名确认**：`validate_dataset(request, dataset)` 以主基线 §21.2 为准（dataset 为独立参数）。A 侧 `EngineProvider` Protocol 与 `RunAnalysisUseCase` 均按该签名实现（`apps/workbench-desktop/app/domain/engine_provider.py`、`application/analysis_usecase.py`），B 需求文档 §4.1 的旧表述（request 内嵌）不再采用——同意 B 在 m0-handover-b.md §6 提出的裁定。

## 6. 已知限制（M0）

- **控制 API 全 stub**：`/api/v1` 13 端点恒 501（`INTERNAL_ERROR` + details 标注 stub）；`require_tenant_access`/`require_developer_scope` 为依赖占位，无真实令牌签发与校验（真实认证 M2）。
- **api_client 离线占位**：`apps/workbench-desktop/app/infrastructure/api_client/client.py` 的 `OfflineApiClient` 不实装网络调用；工作台状态栏"网络：离线"为占位展示，M0 完全离线可运行。
- **分析结果为 fixture**：默认 `WORKBENCH_ENGINE=fake`（FakeEngine 读 `tests/fixtures/fake-analysis.json` 冻结结果，含 `ANALYSIS_PLACEHOLDER` warning）；`local` 真实计算属 M1/M2（引擎 calculators 为存根）。
- **无真实登录**：web `/login` 为占位页，商户/开发者会话与权限路由守卫未实装；Playwright 冒烟测试按 M0 可选项跳过。
- **云端迁移仅 0001**（元数据 + 枚举，无业务表）：本地无 PostgreSQL 时迁移 roundtrip 测试与 `verify_schema.py` 云端分支 skip（CI 由 service 容器执行）；本地 SQLite 的 `analysis_run`/`analysis_result` 为 0005 完整字段的占位前奏。
- **A 侧 CI（platform-ci.yml，Task 9）未在本批交付**：本批验证为本地命令记录，CI 门禁转入下一批（不静默降级）。

## 7. 回滚方式

- **本地 SQLite（local-data）**：在 `local-data/` 目录执行
  ```powershell
  uv run alembic downgrade 0001_meta   # 摘除 analysis_run/analysis_result（db_schema_version 回 local-0001）
  uv run alembic downgrade base         # 全部回滚（local_meta 一并删除，install_instance_id 将重新生成）
  # 指定数据目录：uv run alembic -x data_dir=<目录> downgrade base
  ```
- **云端 PostgreSQL（control-plane）**：先 `docker compose -f docker-compose.dev.yml up -d postgres`，在 `services/control-plane/` 目录执行
  ```powershell
  uv run alembic downgrade base         # drop control_enum/control_meta（完整可逆；连接串取 DATABASE_URL/.env，或 -x database_url=<url>）
  ```
- **git 分支**：M0 无对外发布物（无 wheel/安装包）。合并前回滚 = 丢弃 `feature/a-m0-platform-baseline`（`master` 保持在 B 基线 41f135d）；合并后回滚 = `git revert <merge commit>`，不使用 force push。本地数据目录（`%LOCALAPPDATA%\WarehouseWorkbench\data`）属用户数据，git 不管理，迁移 downgrade 不经手备份文件（备份/恢复脚本 `verify_backup_restore.py` 随 M1+ 交付）。
- 首个需要正式回滚方案的发布物是 M1 的 engine wheel（B）与工作台安装包（A，M6），届时提供上一版本 + 兼容矩阵。

## 8. 后续待办（转入下一批，不静默降级）

1. Task 9：`platform-ci.yml`（uv sync → A 侧 pytest（offscreen）→ ruff/mypy → OpenAPI 重导出 diff → sync-envelope Schema 校验 → pnpm lint/typecheck → PostgreSQL service 容器跑云端迁移测试）。
2. Task 11：对照 §22.2 合并门槛自查 + 按 §26.2 模板提交 PR（附本文件与 B 的 m0-handover-b.md 交叉引用）。
3. B 侧回签：对 A 的 sync-envelope.schema.json / openapi.json 的评审确认（B m0-handover-b.md §5 P0-3 待办）与 formula-spec.md 会签表落笔。
4. 文档修订：README「快速开始」中仓库根直接执行 `uv run --package control-plane uvicorn ...` / `uv run --package workbench-desktop python -m app` 的写法经验证不可用（`uv run --package` 不切换 cwd，`app` 包不在 sys.path，报 `ModuleNotFoundError`）；正确命令见本文 §1（cd 到服务/应用目录后运行）。属文档修正，随下批 PR 更新 README。
