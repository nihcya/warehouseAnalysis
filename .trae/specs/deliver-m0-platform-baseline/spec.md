# 开发者 A：M0 平台工作台基线（控制平面 + Web + 工作台 + 本地库骨架）Spec

## Why

评审报告结论为"有条件通过，准予进入 M0"，G2（开发就绪）未通过。开发者 B 已完成引擎侧 M0 基线（contracts、engine 骨架、FakeEngine、黄金数据，47 测试全绿）并交接（`docs/m0-handover-b.md`）。开发者 A 必须交付平台侧 M0 基线——FastAPI 控制平面骨架、Next.js Web 骨架、PySide6 工作台骨架、本地 SQLite / 云端 PostgreSQL 迁移框架、A 主导的两份公共契约文件（`sync-envelope.schema.json` 与 `openapi.json`）——才能满足 §22.2 合并门槛，让两人进入 M1 并行业务开发而不互相阻塞。

## What Changes

- 新建 `services/control-plane`（uv workspace 包）：FastAPI 分层骨架（api/v1、application、domain、infrastructure、settings、main）、`/health`、`/api/v1` stub 路由、统一错误响应（`error.code/message/details/request_id`）、`require_tenant_access` / `require_developer_scope` 依赖占位、Alembic 环境与 `0001_control_meta` 迁移。
- 新建 `local-data`（uv workspace 包）：SQLAlchemy 2 模型基线、连接工厂（`foreign_keys=ON`、`journal_mode=WAL`、`busy_timeout=5000`、单写入会话）、Alembic 环境与 `0001_meta` 迁移、`analysis_run` / `analysis_result` 持久化占位与 Repository。
- 新建 `apps/workbench-desktop`（uv workspace 包）：PySide6 分层骨架（presentation / application / domain / infrastructure / workers / main.py）、空窗口与总览页占位、`EngineProvider` 依赖注入（`FakeEngineProvider` / `LocalEngineProvider`，`WORKBENCH_ENGINE=fake|local` 组合根切换）、调用 `FakeEngine.from_fixture` 执行"构造请求 → validate → analyze → 进度回调 → 结果展示 → 持久化"最小闭环。
- 新建 `apps/web`（pnpm workspace）：Next.js App Router + TypeScript + Ant Design + TanStack Query 骨架、`(public)` / `(merchant)` / `(developer)` 路由组、登录页占位、`openapi-typescript` 客户端生成脚本。
- 新增 A 主导的两份公共契约文件（五份公共文件的后两份）：`packages/contracts-schema/sync-envelope.schema.json` 与 `packages/contracts-schema/openapi.json`（由 control-plane 导出），并附正反例契约测试。
- 新增工程与文档：根 `package.json`（pnpm workspace）、`.env.example`、`docker-compose.dev.yml`、`DECISIONS.md`、`CONTRIBUTING.md`、`SECURITY.md` 占位、`docs/er-diagram.md`（本地 + 云端两张 ER 图）、`docs/data-dictionary.md`（初版）、`scripts/verify_schema.py`、`docs/m0-handover-a.md`（A 的 M0 交接说明与联调证据）。
- 新增 CI：`.github/workflows/platform-ci.yml`（A 侧 job：pytest、ruff、mypy、web lint/typecheck、OpenAPI 重导出一致性、sync-envelope Schema 校验、PostgreSQL service 容器）。
- Git 操作：从 `origin/master` 最新提交创建 `feature/a-m0-platform-baseline` 分支，完成后按 §26.2 PR 模板提回 `master`。

**职责边界（不在 A 的 M0 范围）**：KPI/ABC/补货/预测公式（B 的 M1+）；导入向导、真实认证/许可证/设备 API 实现、SSE 实装、托盘 Agent 完整实现、打包签名（A 的 M1-M3）；小程序页面（本期不做）。A 不得在 UI 中重新计算任何 KPI，不得把商户完整业务数据上传云端控制库。

## Impact

- Affected specs: `deliver-m0-engine-baseline`（B 的 M0 spec，其遗留协作项 Task 1.5 / 2.5 / 7.1 由本 spec 承接关闭）。
- Affected code: 新建 `services/control-plane/`、`local-data/`、`apps/workbench-desktop/`、`apps/web/`；扩展根 `pyproject.toml`（uv workspace members）、新增根 `package.json` / `pnpm-lock.yaml`；扩展 `packages/contracts-schema/`（新增两份文件）；扩展 `.github/workflows/`；扩展 `docs/`。不修改 B 的 `packages/warehouse-engine` 内部代码。
- 依赖约束：workbench-desktop 仅通过 `warehouse_engine.engine` / `warehouse_engine.fake` 公开入口和 `contracts.analysis` / `contracts.enums` 类型接入；control-plane 仅依赖 contracts-python 与自身分层；Web 仅通过生成的 OpenAPI client；local-data 不依赖 Web/引擎内部。

## ADDED Requirements

### Requirement: 控制平面骨架与健康检查

系统 SHALL 在 `services/control-plane` 提供 FastAPI 应用，挂载 `/health`（返回组件状态与版本）和 `/api/v1` 路由前缀；路由层不直接写 ORM，统一错误响应格式为 `{"error": {"code", "message", "details", "request_id"}}`，错误码使用 `contracts.enums.ErrorCode`。

#### Scenario: 健康检查
- **WHEN** 客户端请求 `GET /health`
- **THEN** 返回 200 与应用版本、数据库可达状态

#### Scenario: 统一错误响应
- **WHEN** 请求未带凭证访问受保护 stub 接口
- **THEN** 返回 401 与 `AUTH_REQUIRED` 错误码及 `request_id`，不返回未捕获异常堆栈

### Requirement: API Scope 骨架与负向测试

系统 SHALL 提供 `require_tenant_access` 与 `require_developer_scope` FastAPI 依赖占位；M0 阶段所有受保护 stub 接口必须挂载其一，并通过负向测试验证：商户 Scope 无法访问开发者接口（`AUTH_FORBIDDEN`）、无凭证返回 `AUTH_REQUIRED`。

#### Scenario: 商户 Scope 访问开发者接口被拒
- **WHEN** 仅持商户 Scope 的请求访问 `developer:*` 接口
- **THEN** 返回 403 与 `AUTH_FORBIDDEN`，不执行任何业务逻辑

### Requirement: 云端控制库初始迁移

系统 SHALL 提供 control-plane 的 Alembic 环境与 `0001_control_meta` 迁移（数据库版本表与基础枚举，按主基线 §35.7 云端迁移顺序第 1 步），空库可重复执行迁移并通过验证；M0 不建立商户业务明细表。

#### Scenario: 空库迁移
- **WHEN** 在全新 PostgreSQL（CI service 容器）上执行 `alembic upgrade head`
- **THEN** 迁移成功，schema 版本记录为 control 0001

### Requirement: 本地业务库初始迁移与连接约束

系统 SHALL 在 `local-data` 提供 SQLAlchemy 2 + Alembic 环境与 `0001_meta` 迁移（数据库版本、安装实例与单主工作台标识）；连接工厂打开连接时执行 `PRAGMA foreign_keys=ON`、`PRAGMA journal_mode=WAL`、`PRAGMA busy_timeout=5000`，数据库路径位于 `%LOCALAPPDATA%\WarehouseWorkbench\data\warehouse.db`（测试中可重定向临时目录），并保证单写入会话。

#### Scenario: 连接 PRAGMA 生效
- **WHEN** 打开本地数据库连接
- **THEN** `foreign_keys` 为 1、`journal_mode` 为 `wal`、`busy_timeout` 为 5000

### Requirement: 分析运行持久化占位

系统 SHALL 提供本地 `analysis_run` / `analysis_result` ORM 模型与 Repository 占位（字段对齐主基线 §35.4：`run_id UNIQUE`、engine/formula 版本、状态、`metric_json`、`warning_json` 等），M0 至少支持将 `AnalysisResult` 原样序列化存入并以 `run_id` 查回。

#### Scenario: 结果存取往返
- **WHEN** 一次 FakeEngine 分析完成后保存结果并按 `run_id` 查询
- **THEN** 取回的结果与保存时的序列化内容一致，且含 engine_version / formula_version

### Requirement: 工作台 FakeEngine 注入与展示（关闭 B 的 Task 7.1）

系统 SHALL 在 `apps/workbench-desktop` 实现 `EngineProvider` Protocol 与 `FakeEngineProvider` / `LocalEngineProvider`，通过环境变量 `WORKBENCH_ENGINE=fake|local` 在组合根切换；M0 默认 `fake`，工作台可执行"构造 `AnalysisRequest` 与 `EngineDataset` → `validate_dataset` → `analyze`（含进度回调）→ 结果表格展示 → 持久化"最小闭环；界面不出现 `if fake` 分支逻辑。

#### Scenario: FakeEngine 联调证据
- **WHEN** 以 `WORKBENCH_ENGINE=fake` 启动工作台并触发示例分析
- **THEN** 界面展示 `FakeEngine.from_fixture("tests/fixtures/fake-analysis.json")` 的结果，含 run_id、engine_version、formula_version、metrics 与 warnings，进度回调驱动进度条

#### Scenario: 校验失败不执行分析
- **WHEN** `validate_dataset` 返回阻断性问题
- **THEN** 界面展示错误列表（定位到字段），不调用 `analyze`

### Requirement: 同步信封契约（A 主导，五份公共文件之一）

系统 SHALL 提供 `packages/contracts-schema/sync-envelope.schema.json`，字段对齐主基线 §21.6：`event_id`、`merchant_id`、`target_device_id`、`idempotency_key`、`algorithm`（`AES-256-GCM`）、`nonce`、`ciphertext`、`created_at`、`expires_at`（created_at < expires_at）；并附正反例契约测试。

#### Scenario: 合法信封通过校验
- **WHEN** 校验 §21.6 样例信封
- **THEN** 通过 Schema 校验

#### Scenario: 非法信封被拒
- **WHEN** 信封缺 `ciphertext`、`algorithm` 非法或 `expires_at <= created_at`
- **THEN** Schema 校验失败并定位到具体字段

### Requirement: OpenAPI 快照与一致性门禁（五份公共文件之二）

系统 SHALL 由 control-plane 导出 `openapi.json` 提交至 `packages/contracts-schema/`，作为 `/api/v1` 唯一接口来源；CI 通过"重导出 + diff"检查快照与代码一致，`openapi-typescript` 生成 Web 客户端类型。

#### Scenario: OpenAPI 漂移被 CI 阻断
- **WHEN** 有人修改路由而未重新导出 `openapi.json`
- **THEN** CI 的一致性检查失败，阻止合并

### Requirement: Web 骨架与路由组隔离

系统 SHALL 在 `apps/web` 建立 Next.js App Router 应用，含 `(public)`、`(merchant)`、`(developer)` 三个路由组、登录页占位与生成的前端类型；M0 不实现真实登录与业务页面。

#### Scenario: 路由组可访问
- **WHEN** 访问官网占位首页与登录占位页
- **THEN** 页面正常渲染，`pnpm lint` 与 `pnpm typecheck` 通过

### Requirement: ER 图与数据字典归档（评审报告 P0-1）

系统 SHALL 在 `docs/er-diagram.md` 归档本地业务库与云端控制库两张 ER 图（对齐主基线 §35.2.1/§35.2.2），并在 `docs/data-dictionary.md` 记录 M0 已建表与同步信封的字段、类型、单位、空值与敏感级别。

#### Scenario: ER 图归档
- **WHEN** 评审 G1 架构冻结材料
- **THEN** `docs/er-diagram.md` 包含本地与云端两张 Mermaid ER 图且与迁移一致

### Requirement: CI 与质量门禁（A 侧）

系统 SHALL 建立 `platform-ci.yml`：control-plane / local-data / workbench-desktop 的 pytest（PySide6 测试以 offscreen 平台运行）、A 侧 ruff 与 mypy、web lint/typecheck、OpenAPI 重导出 diff、sync-envelope Schema 校验、PostgreSQL service 容器跑云端迁移测试；且不得使 B 的既有 47 个测试回归。

#### Scenario: 全量检查通过
- **WHEN** 在 CI 执行 A 侧全量 job
- **THEN** 全部通过，`uv run pytest` 在本地同样全绿

### Requirement: M0 交接与协作项关闭

系统 SHALL 输出 `docs/m0-handover-a.md`（安装命令、公开入口、验证记录、已知限制、回滚方式），并记录三项协作证据：Task 1.5（A 提交两份公共文件并对 B 的三份文件给出评审结论）、Task 2.5（对 `docs/formula-spec.md` 的走查结论）、Task 7.1（FakeEngine 联调证据）。

#### Scenario: B 的待办协调点可关闭
- **WHEN** B 阅读 `docs/m0-handover-a.md`
- **THEN** 三项协作待办均有 A 侧证据与结论，G2 门槛材料齐备

## MODIFIED Requirements

（无——本 spec 全部为新增能力，不修改既有需求。）

## REMOVED Requirements

（无。）
