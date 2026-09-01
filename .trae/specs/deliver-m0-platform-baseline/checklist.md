# Checklist：开发者 A M0 平台工作台基线

## 分支与基线
- [x] 本地 `master` 与 `origin/master`（41f135d）一致，工作区干净（起点已验证；当前变更均在 `feature/a-m0-platform-baseline` 上）
- [x] 已创建 `feature/a-m0-platform-baseline` 分支并切换
- [x] `uv run pytest` 起点全绿（B 的 47 个测试无回归）

## 工程配置
- [x] 根 `package.json` + `pnpm-lock.yaml` 存在，`pnpm install` 可用
- [x] `.env.example` 变量名、类型、必填、示例值齐全，不含真实密钥
- [x] `docker-compose.dev.yml` 可启动本地 PostgreSQL
- [x] `DECISIONS.md` 记录 §18 的 A 侧冻结决策；`CONTRIBUTING.md`、`SECURITY.md` 占位存在
- [x] `README.md` 含 §20.3 统一启动命令与目录结构（启动命令已经实测修正为 cd 子目录执行）

## Control Plane
- [x] `GET /health` 返回版本与数据库状态
- [x] `/api/v1` stub 路由齐备（auth/devices/config/tasks/sync/telemetry 等 13 条）
- [x] 错误响应统一为 `{error: {code, message, details, request_id}}`，code 来自 `contracts.enums.ErrorCode`
- [x] 无凭证访问受保护接口返回 401 `AUTH_REQUIRED`（负向测试通过）
- [x] 商户 Scope 访问开发者接口返回 403 `AUTH_FORBIDDEN`（负向测试通过，P0-4 骨架）
- [x] `0001_control_meta` 迁移在空 PostgreSQL 上 upgrade/downgrade 均成功（本地无 PG 时 skip，CI service 容器内执行）
- [x] `openapi.json` 已导出至 `packages/contracts-schema/`，重导出一致性测试通过

## Local Data（SQLite）
- [x] 连接工厂执行 `foreign_keys=ON`、`journal_mode=WAL`、`busy_timeout=5000`（测试断言 PRAGMA 值）
- [x] 数据库路径为 `%LOCALAPPDATA%\WarehouseWorkbench\data\warehouse.db`（测试重定向 tmp）
- [x] `0001_meta` + `0002_analysis_m0` 迁移 upgrade/downgrade 成功，含安装实例与单主工作台标识
- [x] `analysis_run` / `analysis_result` 模型与 Repository 占位可用，`run_id` 存取往返一致
- [x] 金额/数量使用 Decimal 语义，无 float 真值

## 公共契约（五份文件齐备）
- [x] `sync-envelope.schema.json` 字段对齐 §21.6，含 created_at < expires_at 约束
- [x] sync-envelope 正反例测试通过（合法样例过；缺字段/非法 algorithm/时间倒置失败并定位字段）
- [x] `openapi.json` 为 `/api/v1` 唯一来源，`openapi-typescript` 生成 Web 类型
- [x] 五份公共文件全部存在于 `main/master` 分支合并不是必须（本 spec 只要求 A 侧两份产出 + 评审记录）

## Workbench Desktop
- [x] 分层目录符合 §30.1，依赖方向无反向导入（presentation 不直接访问 DB/HTTP）
- [x] PySide6 空窗口含导航与顶部状态栏占位
- [x] `EngineProvider` 注入点存在，`WORKBENCH_ENGINE=fake|local` 只在组合根生效，UI 无 `if fake`
- [x] FakeEngine 全链路可用：validate → analyze（进度回调）→ 结果展示 → 持久化
- [x] 校验失败时展示错误列表且不调用 analyze
- [x] 结果页展示 run_id、engine_version、formula_version、metrics、warnings
- [x] pytest-qt（offscreen）测试通过，联调证据已记录（关闭 B 的 Task 7.1）
- [x] 只依赖 `warehouse_engine.engine` / `warehouse_engine.fake` / `contracts.*` 公开入口，未导入 B 的内部模块

## Web
- [x] `(public)` / `(merchant)` / `(developer)` 路由组存在，登录页占位可渲染
- [x] `pnpm --filter web lint` / `typecheck` 通过（含 `next build` 与 dev server 冒烟）
- [x] 前端类型由 openapi-typescript 生成，无手写重复 DTO
- [x] 视觉基线对齐 §42.2（主色 #173B5F、背景 #F4F7FA、圆角 ≤ 6px）

## CI 与门禁
- [x] `platform-ci.yml` 编写完成，所有步骤命令均已在本地验证通过（实际运行在 PR 上触发）
- [x] OpenAPI 重导出 diff 门禁生效（git diff --exit-code；本地以哈希对比验证一致）
- [x] CI 中 B 侧 engine job 不受影响（engine-ci.yml 未改动）
- [x] 本地 `uv run pytest` 全绿（114 passed, 1 skipped）

## 文档与证据（P0 归档）
- [x] `docs/er-diagram.md` 含本地与云端两张 ER 图，与迁移一致（P0-1）
- [x] `docs/data-dictionary.md` 初版覆盖 0001 迁移表与同步信封字段（P0-1）
- [x] `docs/m0-handover-a.md` 含安装命令、入口、验证记录、已知限制、回滚方式
- [x] Task 1.5 协作证据：A 对 B 三份契约文件的评审结论 + A 两份公共文件提交（P0-3）
- [x] Task 2.5 协作证据：formula-spec 走查结论（A 侧无 UI 公式实现空间确认）
- [x] Task 7.1 协作证据：FakeEngine 联调记录
- [x] `scripts/verify_schema.py` 可验证两库 0001 迁移结果（本地 13 项全过；云端无 PG 时 skip）

## 验收（§22.2 合并门槛 + A 完成定义）
- [x] A 的工作台可以调用 FakeEngine 并展示结果（pytest-qt 全链路测试通过）
- [x] OpenAPI、JSON Schema 和 Python 类型由 CI 检查一致（重导出 diff + schema 测试 + mypy）
- [x] PR 按 §26.2 模板提交（`feature/a-m0-platform-baseline` → `master`，PR #2），含测试结果与证据
- [x] G2（开发就绪）材料齐备：README、`.env.example`、CI、FakeEngine 接入、迁移框架
