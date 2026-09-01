# Tasks：开发者 A M0 平台工作台基线

依据：`开发规划与协作需求文档.md` V2.0 §20/§21/§22.2/§26/§30/§31.1-31.2/§35/§37/§39、`开发需求-A平台工作台.md` §4 M0、`项目开发文档评审报告.md` §8 P0-1/P0-3/P0-4、`docs/m0-handover-b.md` §8 协作待办。

- [x] Task 1: Git 分支与 B 基线验证
  - [x] 1.1 `git fetch origin` 并确认本地 `master` 与 `origin/master`（commit 41f135d）一致；从 `master` 创建并切换到 `feature/a-m0-platform-baseline` 分支
  - [x] 1.2 在新分支上执行 `uv sync --all-packages --group dev` 与 `uv run pytest`，确认 B 的 47 个测试全绿（起点绿灯，避免在红灯上开发）

- [x] Task 2: monorepo 工程配置与决策记录
  - [x] 2.1 根 `package.json`（pnpm workspace：`apps/web`）+ `pnpm-lock.yaml`；Node 22 / pnpm 版本写入 README（pnpm-lock 随 Task 8 `pnpm install` 生成）
  - [x] 2.2 `.env.example`：变量名、类型、是否必填、示例值（DATABASE_URL、WORKBENCH_ENGINE、环境分层 local/ci/staging/production）
  - [x] 2.3 `docker-compose.dev.yml`：本地 PostgreSQL 容器（端口、卷、健康检查）
  - [x] 2.4 `DECISIONS.md`：记录 §18 中 A 侧已冻结决策（单主工作台、SQLite 路径与 PRAGMA、SSE、托盘 Agent、DPAPI、签名策略、离线宽限方向）；`CONTRIBUTING.md` 与 `SECURITY.md` 最小占位
  - [x] 2.5 更新根 `README.md`：统一启动命令（§20.3 PowerShell）与目录结构说明

- [x] Task 3: control-plane FastAPI 骨架
  - [x] 3.1 `services/control-plane` uv 包，按 §30.3 分层建目录（api/v1、application、domain、infrastructure、settings.py、main.py）；挂入根 workspace
  - [x] 3.2 `GET /health`：返回应用版本与数据库可达状态
  - [x] 3.3 `/api/v1` stub 路由（auth、devices、config、tasks、sync、telemetry 空壳，声明路径/方法/Scope/幂等要求，返回统一"未实现"错误）
  - [x] 3.4 统一异常处理器与错误响应 `{"error": {code, message, details, request_id}}`，错误码取自 `contracts.enums.ErrorCode`
  - [x] 3.5 `require_tenant_access` / `require_developer_scope` 依赖占位 + 负向测试（无凭证 → 401 `AUTH_REQUIRED`；商户 Scope 访问开发者接口 → 403 `AUTH_FORBIDDEN`）
  - [x] 3.6 OpenAPI 导出脚本：应用启动导出 `openapi.json` → 提交至 `packages/contracts-schema/openapi.json`；配套重导出一致性测试（与 B 的 test_schema_sync 风格一致）

- [x] Task 4: 云端 PostgreSQL Alembic 与 0001_control_meta
  - [x] 4.1 control-plane Alembic 环境（异步引擎可选同步，配置走 settings/env）
  - [x] 4.2 `0001_control_meta` 迁移：数据库版本表与基础枚举（§35.7 云端第 1 步；含 `downgrade` 与回滚说明）
  - [x] 4.3 空库迁移测试：对临时 PostgreSQL（docker compose 或 CI service）执行 `alembic upgrade head` + 验证 + `downgrade` 回滚验证（本地无 PG 时 skip，CI service 容器内执行）

- [x] Task 5: local-data SQLite 包
  - [x] 5.1 `local-data` uv 包：SQLAlchemy 2 declarative Base、元数据分组（主数据/事件/分析/同步占位）
  - [x] 5.2 连接工厂：`foreign_keys=ON`、`journal_mode=WAL`、`busy_timeout=5000`；数据目录 `%LOCALAPPDATA%\WarehouseWorkbench\data`（测试重定向 tmp）；单写入会话封装
  - [x] 5.3 Alembic 环境与 `0001_meta` + `0002_analysis_m0` 迁移：数据库版本、安装实例、单主工作台标识（§35.7 本地第 1 步）；含 downgrade
  - [x] 5.4 `analysis_run` / `analysis_result` ORM 模型 + Repository 占位（字段对齐 §35.4；`run_id UNIQUE`、engine/formula 版本、状态机字段、`metric_json`/`warning_json`）
  - [x] 5.5 测试：空库迁移/回滚、PRAGMA 生效、结果存取往返（按 run_id 存取一致）

- [x] Task 6: A 主导的两份公共契约文件（关闭协作待办 Task 1.5 的 A 侧）
  - [x] 6.1 `packages/contracts-schema/sync-envelope.schema.json`：字段对齐 §21.6 信封样例（event_id、merchant_id、target_device_id、idempotency_key、algorithm=AES-256-GCM、nonce、ciphertext、created_at、expires_at；约束 created_at < expires_at、幂等键格式）
  - [x] 6.2 `packages/contracts-schema/openapi.json` 由 Task 3.6 导出提交（五份公共文件齐备）
  - [x] 6.3 `tests/contract/` 新增 sync-envelope 正反例测试（合法样例通过；缺字段、非法 algorithm、expires_at 早于 created_at 失败并定位字段）与 openapi 快照存在性/版本检查
  - [x] 6.4 在 `docs/m0-handover-a.md` 中给出对 B 的三份文件（enums.py、analysis.py、两份 JSON Schema）的评审结论；确认 `validate_dataset(request, dataset)` 签名以主基线 §21.2 为准

- [x] Task 7: workbench-desktop PySide6 骨架（关闭协作待办 Task 7.1）
  - [x] 7.1 `apps/workbench-desktop` uv 包，按 §30.1 分层建目录（presentation/application/domain/infrastructure/db、engine_adapter、api_client、backup、logging/workers/main.py）；挂入根 workspace
  - [x] 7.2 `main.py` 启动 PySide6 空窗口：左侧导航占位（总览/货品/库存流水/分析/调度/报告/备份/设置）+ 顶部状态栏占位（网络/授权/待同步/版本）
  - [x] 7.3 `EngineProvider` Protocol + `FakeEngineProvider` / `LocalEngineProvider`；`WORKBENCH_ENGINE=fake|local` 仅在组合根读取，UI 无 `if fake`
  - [x] 7.4 `application` 分析用例：构造 `AnalysisRequest` + `EngineDataset`（读取 `tests/fixtures/golden/v0.1.0/input.json`）→ `validate_dataset` → `analyze(progress)` → 保存 `AnalysisResult`
  - [x] 7.5 presentation 分析结果页：进度条（进度回调驱动）+ 结果表格（run_id、engine_version、formula_version、metrics、warnings）；校验失败时展示错误列表且不调用 analyze
  - [x] 7.6 infrastructure/db 接 local-data Repository，结果原样持久化并可按 run_id 重新查看
  - [x] 7.7 pytest-qt（offscreen）测试：FakeEngine 全链路（validate → analyze → 展示 → 持久化）+ 联调证据（命令、输出、截图说明）记入 `docs/m0-handover-a.md`
  - [x] 7.8 `api_client` 目录占位（依据 openapi.json 的客户端骨架与离线状态占位，不实装网络调用）

- [x] Task 8: apps/web Next.js 骨架
  - [x] 8.1 `apps/web`：Next.js App Router + TypeScript + Ant Design + TanStack Query 初始化；视觉基线对齐 §42.2（主色 #173B5F、背景 #F4F7FA、圆角 ≤ 6px）
  - [x] 8.2 路由组 `(public)`/`(merchant)`/`(developer)` + 官网首页占位 + 登录页占位
  - [x] 8.3 `openapi-typescript` 生成脚本：从 `packages/contracts-schema/openapi.json` 生成 `apps/web/src/api/types`；禁止手写请求 DTO
  - [x] 8.4 `pnpm --filter web lint` / `typecheck` 通过（含 next build 验证）；Playwright 冒烟测试已按 M0 可选项跳过

- [x] Task 9: CI 与门禁（A 侧）
  - [x] 9.1 `.github/workflows/platform-ci.yml`：uv sync → A 侧 pytest（QT_QPA_PLATFORM=offscreen）→ ruff → mypy → OpenAPI 重导出 diff（git diff --exit-code）→ sync-envelope Schema 校验 → pnpm lint/typecheck；PostgreSQL service 容器供云端迁移测试
  - [x] 9.2 全量 `uv run pytest` 通过且 B 的 47 个测试无回归；本地按 §25.3 A 侧命令全绿

- [x] Task 10: 文档与证据归档
  - [x] 10.1 `docs/er-diagram.md`：本地业务库与云端控制库两张 Mermaid ER 图（源自主基线 §35.2.1/§35.2.2，字段与迁移对齐）
  - [x] 10.2 `docs/data-dictionary.md` 初版：0001_meta / 0001_control_meta 涉及表 + 同步信封字段的类型、单位、空值、来源、敏感级别
  - [x] 10.3 `docs/m0-handover-a.md`：安装命令、公开入口、验证记录（Task 1.5/2.5/7.1 三项协作证据）、已知限制、回滚方式（P0-1/P0-3 归档证据）
  - [x] 10.4 `scripts/verify_schema.py`：对本地与云端 0001 迁移结果做表/约束/版本检查（M0 范围）

- [x] Task 11: M0 验收与 PR
  - [x] 11.1 对照 §22.2 合并门槛自查：工作台调用 FakeEngine 展示结果；OpenAPI/JSON Schema/Python 类型 CI 一致
  - [x] 11.2 按 §26.2 模板提交 PR（`feature/a-m0-platform-baseline` → `master`，PR #2），附测试结果与协作证据链接

# Task Dependencies

- Task 2 依赖 Task 1（分支就绪）
- Task 3、Task 5 可并行（互不依赖）；Task 4 依赖 Task 3（control-plane 包存在）
- Task 6.2 依赖 Task 3.6（openapi 导出）；Task 6.1/6.3 仅依赖 Task 1
- Task 7 依赖 Task 5（Repository）与 Task 6（契约冻结，实际依赖 B 已交付的 contracts）
- Task 8 依赖 Task 3.6（openapi.json 存在）
- Task 9 依赖 Task 3-8
- Task 10、Task 11 依赖全部前置任务
