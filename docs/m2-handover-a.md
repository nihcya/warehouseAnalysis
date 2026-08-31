# 开发者 A：M2 交接说明（控制平面认证授权与商户端状态验收材料）

**日期**：2026-08-30  
**交付人**：开发者 A（平台集成：control-plane、apps/web、契约文件、文档）  
**对应里程碑**：M2 控制平面认证授权与商户端状态（`开发需求-A平台工作台.md` §4 M2 第 4-7 周；`开发规划与协作需求文档.md` §35.6/§35.7/§32.3/§10.3）  
**代码分支**：`feature/a-m2-control-plane-auth`（自合并 PR #4 后的 `master`，基准 `d7e1468` 切出）  
**版本基线**：control-plane **0.1.0 → 0.2.0**（新增 3 支迁移 `control-0002/0003/0004`）、本地库 schema `local-0006`（M1 终态，M2 未动）、云端库 schema `control-0001 → control-0004`、web 0.1.0（未动版本号）、contracts 1.0（**零改动**）、engine 0.1.0 / formula 0.1.0-draft  
**对应文档**：`docs/data-dictionary.md` §2.3~~2.10 与 §4~~§5 版本记录、`docs/er-diagram.md` §2.2~~2.3、`DECISIONS.md` D-013~~D-020、`CHANGELOG.md` A 侧 0.2.0 条目、`.trae/specs/deliver-m2-control-plane-auth/{spec,tasks}.md`

---

## 1. 安装与启动命令

```powershell
# 环境安装（Python 3.11 由 uv.lock 锁定；Web 另需 Node.js 22 LTS + pnpm）
uv sync --all-packages --group dev
pnpm install

# 本地 PostgreSQL（可选：生产路径 / 云端迁移测试用；无 PG 时内存仓储与 PG 迁移测试自动 skip）
docker compose -f docker-compose.dev.yml up -d postgres

# 启动云端控制平面（默认 PostgreSQL 仓储；无 PG 时改用 memory 仓储演示）
cd services/control-plane
uv run uvicorn app.main:app --reload --port 8000            # 生产路径
#   —— 或本地无 PG 快速演示 ——
$env:CONTROL_PLANE_REPOSITORY="memory"; uv run uvicorn app.main:app --reload --port 8000
#   memory 仓储会播种演示账号（见 §3.1）；生产环境（APP_ENV=production）拒绝 memory

# 启动 Web（商户端页面接真实 API）
pnpm --filter web dev提交一下PR再试试。

# 全量检查
uv run pytest                                            # 342 passed, 1 skipped（见 §6）
uv run ruff check services/control-plane/app services/control-plane/tests
uv run mypy services/control-plane/app                  # 单批：control-plane 与 web 顶层包不重名
pnpm --filter web lint
pnpm --filter web typecheck
pnpm --filter web build
```

**配置项**（`.env.example` 已增列，见 §3.1）：`AUTH_SECRET`、`CONTROL_PLANE_REPOSITORY`（postgres|memory，默认 postgres）、`CONTROL_PLANE_DEMO_PASSWORD`（仅 memory 读取）、`LICENSE_OFFLINE_GRACE_DAYS`（默认 7）、`DATABASE_URL`、`CORS_ORIGINS`、`NEXT_PUBLIC_API_BASE_URL`。

## 2. M2 目标与范围

**目标**：把控制平面从 M0/M1 的"占位骨架"（全部端点恒 501、dev token 字符串鉴权、仅 2 张元数据表、Web 三端占位）推进为"可登录、可授权、可追溯、可实时看状态"，为 M3 托盘 Agent（心跳/任务/同步）铺好底座。

**范围取舍**（按主基线 §26.1"功能分支保持 1-3 天、禁止大包"）：本批次取 M2 五项中的第 1、2、4、5 项，**开发者端页面（第 3 项）转入下一分支**。

| 取项             | 内容                                             | 状态                          |
| -------------- | ---------------------------------------------- | --------------------------- |
| M2-1 认证与授权     | 登录/刷新/注销/me、设备注册、许可证与离线宽限期、Scope 与租户隔离、审计      | ✅ 实装                        |
| M2-2 商户端页面     | 登录页接真实 API、商户工作台（许可证/设备/版本状态）、任务与同步积压标注 M3 待开放 | ✅ 实装                        |
| M2-4 Scope 与审计 | dev token 下线、真实 JWT scope 鉴权、5 类管理操作留痕         | ✅ 实装                        |
| M2-5 实时状态      | SSE 状态流 + 30 秒轮询降级                             | ✅ 实装                        |
| M2-3 开发者端页面    | 开发者工作台（商户/许可证/配置管理 UI）                         | ⏭️ 转入 `feature/a-m2-*` 下一分支 |

**明确不动**：`/heartbeat`、`/tasks*`、`/sync/*`、`/config`、`/telemetry`、`/merchants` 维持 stub 501（M3 范围）；contracts 包零改动（错误码仅取 `contracts.enums.ErrorCode` 已冻结值）；不触碰 B 侧 `packages/warehouse-engine`、`skills/`；不改动本地库与桌面工作台。

## 3. 已完成工作与功能模块

### 3.1 公开 API 入口（本次实装）

统一成功响应包 `{"data": ...}`；错误包 `{"error": {"code", "message", "details"}}`，错误码取自 `contracts.enums.ErrorCode`（如 `AUTH_REQUIRED` / `AUTH_FORBIDDEN` / `LICENSE_EXPIRED` / `DEVICE_LIMIT_EXCEEDED` / `DEVICE_REVOKED`，见 `api/v1/errors.py`）。

| 入口       | 方法/路径                                                                                                   | 说明                                                                                                           |
| -------- | ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| 登录       | `POST /api/v1/auth/login`                                                                               | 校验登录名 + Argon2id 哈希；成功返回 Access（JWT 15min）+ Refresh（一次性、仅存 SHA-256 指纹）+ `account`/`tenant`/`license`（含宽限期评估） |
| 刷新       | `POST /api/v1/auth/refresh`                                                                             | 轮换 Refresh Token（旧指纹移入 `previous`，二次使用即重放检测）；重放 → 吊销该账号全部会话，401 `AUTH_REQUIRED`                              |
| 注销       | `POST /api/v1/auth/logout`                                                                              | 撤销当前会话与全部 Refresh 指纹；重复注销幂等 200                                                                              |
| 当前账号     | `GET /api/v1/account/me`                                                                                | 返回账号/租户/许可证状态（需 Access Token）                                                                                |
| 设备注册     | `POST /api/v1/devices/register`                                                                         | 同 `(tenant_id, fingerprint)` 幂等返回既有设备；达 `max_devices` → 403；已吊销指纹 → 403 `DEVICE_REVOKED`                     |
| 设备列表     | `GET /api/v1/devices`                                                                                   | 仅返回令牌声明 `tenant_id` 下的设备（租户隔离）                                                                               |
| 状态流      | `GET /api/v1/events/stream`                                                                             | SSE：单调事件 ID + `Last-Event-ID` 续传 + 15s keepalive；写操作（设备注册/许可证变更）向事件中心发布                                      |
| 状态快照     | `GET /api/v1/events/snapshot?since=`                                                                    | 轮询降级入口，返回 `since` 之后的事件（客户端 30s 轮询）                                                                          |
| stub 501 | `/heartbeat`、`/tasks`、`/tasks/pull`、`/sync/events/pull`、`/sync/ack`、`/config`、`/telemetry`、`/merchants` | M3 范围，保留 `details.stub` 标注；鉴权依赖已改为真实 JWT（测试须登录后携带令牌）                                                         |

### 3.2 商户端页面（apps/web）

| 入口       | 位置                                               | 说明                                                      |
| -------- | ------------------------------------------------ | ------------------------------------------------------- |
| 登录页      | `apps/web/src/app/(public)/login/page.tsx`       | 接真实 `/auth/login`，令牌存 `localStorage`，登录后跳转 `/dashboard` |
| 商户工作台    | `apps/web/src/app/(merchant)/dashboard/page.tsx` | 展示许可证与授权（状态/到期/宽限剩余）、设备列表、客户端版本；任务与同步积压标注"M3 待开放"占位     |
| API 客户端  | `apps/web/src/api/client.ts`                     | 真实 fetch 封装 + `NEXT_PUBLIC_API_BASE_URL` + Bearer 注入    |
| 状态流 hook | `apps/web/src/hooks/use-status-stream.ts`        | SSE 主通道；失败/异常降级 30s 轮询，界面明示"通道：SSE / 轮询降级"              |

### 3.3 持久化（新增 3 支云端迁移）

| 迁移                                                 | revision       | 表                                         |
| -------------------------------------------------- | -------------- | ----------------------------------------- |
| `alembic/versions/0002_tenant_account_device.py`   | `control_0002` | tenant / account / session / device       |
| `alembic/versions/0003_license_product_feature.py` | `control_0003` | product_profile / license / feature_grant |
| `alembic/versions/0004_audit.py`                   | `control_0004` | audit_log（自 §35.7 规划的 0006 **提前**，理由见 §4） |

字段与约束 100% 对齐 `docs/data-dictionary.md` §2.3~2.10；`control_meta.db_schema_version` 升 `control-0004`。枚举种子扩至 67 行（新增 `tenant_status`/`account_status`/`account_role`/`client_type`/`product_profile_status`/`license_status`/`audit_action`/`audit_result`，见 `docs/er-diagram.md` §2.1）。

### 3.4 演示数据（memory 仓储）

设 `CONTROL_PLANE_REPOSITORY=memory` 启动后自动播种，可直接登录：

- 商户账号：`merchant_demo`；开发者账号：`developer_demo`
- 密码：取环境变量 `CONTROL_PLANE_DEMO_PASSWORD`；**未设置时生成一次性随机密码并打印到启动日志**（固定口令不写进代码，遵循 `SECURITY.md`）
- 演示商户 `tnt_demo` / 行业类型 `ppf_demo`（retail）/ 许可证有效期 365 天、`max_devices=3`、功能 `inventory-kpi`+`abc-aging`

```powershell
# 记忆体演示（无 PG 也能跑通完整认证闭环）
$env:CONTROL_PLANE_REPOSITORY="memory"; $env:CONTROL_PLANE_DEMO_PASSWORD="changeme123"
cd services/control-plane; uv run uvicorn app.main:app --reload --port 8000
# 另开终端：pnpm --filter web dev，浏览器开 http://localhost:3000 用 merchant_demo / changeme123 登录
```

## 4. 关键架构与技术决策及其理由

以下决策已冻结为 `DECISIONS.md` D-013~D-020（按基线 §18 要求，变更须新建 ADR 并经 A、B 联合评审）。

| #     | 决策            | 结论                                                                                                                                               | 理由                                                                                    |
| ----- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| D-013 | 密码哈希          | **Argon2id**（`argon2-cffi`）                                                                                                                      | 抗 GPU/侧信道，满足 `SECURITY.md` 基线；明文不落库                                                   |
| D-014 | 令牌模型          | 无状态 **JWT Access（15min）+ 可轮换 Refresh**（Refresh 仅存 **SHA-256 指纹**）                                                                                | 无状态便于水平扩展；每次请求回查 `session` 表使令牌可即时撤销（区别于纯无状态 JWT 的不可吊销）                               |
| D-015 | Refresh 轮换与重放 | 一次性轮换；`previous_refresh_token_hash` 再次出现 → 判定重放，吊销该账号**全部会话**                                                                                    | 重放即意味着令牌泄露，宁可误杀同账号会话也不留口子；代价是粗粒度撤销（见 §7 风险 R-3）                                       |
| D-016 | 离线宽限期         | 默认 **7 天**（`LICENSE_OFFLINE_GRACE_DAYS` 可配，0 = 到期即限），**不落表**，按"到期日 + 宽限天数"推导                                                                     | 避免许可证表与配置双写；`ACTIVE`/`GRACE` 放行，`EXPIRED`/`REVOKED`/`MISSING` → 403 `LICENSE_EXPIRED` |
| D-017 | 仓储双实现         | 定义 `application/ports.py` 仓储协议；**内存实现**（测试注入 / 本地演示）+ **PostgreSQL 实现**（生产），组合根 `container.py` 按 `CONTROL_PLANE_REPOSITORY` 选择；**生产环境拒绝 memory** | 本机无 PG/Docker 时认证等逻辑可 100% 本地验证；PG 迁移由 CI 的 PG service 容器验证（同语义）                      |
| D-018 | SSE 状态通道      | `EventHub`：单调事件 ID + 环形缓冲 + `Last-Event-ID` 续传 + 15s keepalive；客户端 30s 轮询降级（延续 D-006）                                                            | 第一版不引入 WebSocket；SSE 实现简单、断线可续传，轮询兜底保证弱网下仍可见状态                                        |
| D-019 | 审计            | `audit_log` **只追加**（仓储不提供改/删入口）；`detail_json` 经白名单 `ALLOWED_DETAIL_KEYS` 过滤                                                                      | 安全审计默认保留 180 天（§35.10）；不存密码、令牌与业务明细原文                                                 |
| D-020 | dev token 下线  | M2 起 `Bearer merchant` / `Bearer developer` 字符串**不再被接受**，必须真实 JWT                                                                                | 关闭 M0 临时鉴权后门；缺失/无效令牌 → 401 `AUTH_REQUIRED`，scope 不足 → 403 `AUTH_FORBIDDEN`            |

**分层结构**（与 M1/m0 一致，未引入新范式）：  
`domain/`（account、session、device、license、tenant、catalog、audit）→ `infrastructure/`（auth：passwords/tokens/scopes/ids；db：models/repositories；memory：repositories/seed；realtime：hub）→ `application/`（ports、errors、auth_usecase、device_usecase、license_usecase、audit、container）→ `api/v1/`（deps、schemas、routes、errors）。

## 5. 演示路径

### 5.1 CLI/脚本化演示（可重复，无 UI）

```powershell
# 控制平面测试全集（含认证闭环、刷新重放、租户隔离、宽限期、设备上限、审计、SSE 续传、仓储双实现一致性）
cd services/control-plane; QT_QPA_PLATFORM=offscreen uv run pytest -q
# 全量（根 + 本地库 + 桌面 + 控制平面）
cd 仓库根; uv run pytest -q
```

### 5.2 浏览器手动演示（memory 仓储）

1. 启动：`$env:CONTROL_PLANE_REPOSITORY="memory"; $env:CONTROL_PLANE_DEMO_PASSWORD="changeme123"` 后 `cd services/control-plane; uv run uvicorn app.main:app --reload --port 8000`。
2. Web：`pnpm --filter web dev`，开 `http://localhost:3000` → 登录页用 `merchant_demo` / `changeme123` 登录。
3. 登录成功跳转 `/dashboard`：可见许可证状态（`ACTIVE`/宽限剩余天数）、设备列表（空，待 M3 心跳上报）、版本状态；状态流区域显示"通道：SSE"，断网后自动切"轮询降级"。
4. 设备注册（M3 心跳前的占位验证）：用 `POST /api/v1/devices/register` 携带 fingerprint 注册，重复同指纹幂等返回既有设备；设备数达 `max_devices=3` 后第 4 台被拒（403 `DEVICE_LIMIT_EXCEEDED`）。
5. 注销：调用 `POST /api/v1/auth/logout`，旧 Access 失效；重复注销幂等 200。

## 6. 验证记录（2026-08-30，分支 `feature/a-m2-control-plane-auth`）

环境：Windows + CPython 3.11（uv.lock 锁定）+ Node 22 LTS。

### 6.1 质量门禁

| 检查            | 命令                                                                          | 结果                                                                                                                                                   |
| ------------- | --------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| 全量测试          | `uv run pytest`                                                             | **342 passed, 1 skipped**（343 collected；skip = 云端迁移 roundtrip `services/control-plane/tests/test_migrations.py`，本地无 PostgreSQL，CI 由 PG service 容器执行） |
| 控制平面测试        | `uv run pytest services/control-plane`                                      | **89 passed, 1 skipped**（M1 基线 34 → M2 净增 55 例，覆盖认证闭环/刷新重放/租户隔离/Scope 越权/宽限期/设备上限/审计/SSE 续传/仓储双实现一致性）                                                |
| Python 静态检查   | `uv run ruff check services/control-plane/app services/control-plane/tests` | All checks passed（CI 口径 `ruff check .` 等价）                                                                                                           |
| 类型检查          | `uv run mypy services/control-plane/app`                                    | Success: no issues found in **43 source files**                                                                                                      |
| Web lint      | `pnpm --filter web lint`                                                    | 0 errors（eslint）                                                                                                                                     |
| Web typecheck | `pnpm --filter web typecheck`                                               | 0 errors（`tsc --noEmit`）                                                                                                                             |
| Web build     | `pnpm --filter web build`                                                   | 成功，产出 `.next`（7 routes：/、/login、/dashboard、/developers、/\_not-found 等静态预渲染）                                                                          |

> 注：本地无 PostgreSQL，PG 迁移 roundtrip 与双实现"PG 侧"语义由 CI 的 PostgreSQL service 容器执行；memory 侧 100% 本地通过，保证逻辑正确。

### 6.2 真实服务端到端冒烟（memory 仓储，uvicorn 端口 8000）

- 登录 `/auth/login` → 200，返回 access/refresh/account/tenant/license ✅
- 刷新 `/auth/refresh` → 新令牌对，旧 refresh 二次使用 401 `AUTH_REQUIRED`（重放检测）✅
- 设备注册 `/devices/register` → 幂等；超 `max_devices` 403 `DEVICE_LIMIT_EXCEEDED` ✅
- SSE `/events/stream` 携带 `Last-Event-ID` 重连 → 先补发缓冲事件再推当前快照 ✅
- 审计 `audit_log` → 登录成功/失败、刷新、注销、设备注册、许可证变更均留痕，`detail_json` 无敏感原文 ✅

## 7. 对 B 的协作说明

**契约零改动声明**：M2 未修改 `contracts` 包（1.0 冻结遵守）。本批次 A 侧新增的错误码（如 `DEVICE_LIMIT_EXCEEDED`、`DEVICE_REVOKED`、`LICENSE_EXPIRED`）取自 `contracts.enums.ErrorCode` 已冻结值，未引入新枚举；若 B 在 M3 需要新增错误码，请按"只加不改"扩展并同步 `openapi.json` 重导出（CI 漂移门禁会拦截）。

1. **M3 端点依赖本批次底座**：`/heartbeat`、`/tasks*`、`/sync/*`、`/config`、`/telemetry`、`/merchants` 现均为 501 stub，但鉴权已接真实 JWT（scope=developer 才可访问 `/merchants`）。B 落地这些端点时直接复用 `api/v1/deps.py` 的 `require_developer_scope` / `require_tenant_access` / `require_tenant_with_license`，无需重新实现鉴权。
2. **SSE 事件类型待约定**：当前 `EventHub` 发布 `device.registered` / `license.changed` 两类事件，M3 心跳/任务/同步事件建议沿用同一 `StatusEvent` 结构（`id`/`type`/`payload`/`ts`），由 B 在 M3 扩展事件类型枚举，避免各自定义。
3. **审计动作枚举可扩展**：`audit_action` 当前 5 值（AUTH_LOGIN/REFRESH/LOGOUT/DEVICE_REGISTER/LICENSE_STATUS_CHANGE），M3 心跳/任务/同步相关动作走"只加不改"追加（CHECK 约束，新增不破坏旧值）。
4. **统一错误响应包**：成功 `{"data":...}`、错误 `{"error":{...}}`，与 M0/M1 契约风格一致；B 的端点请沿用，勿新增响应形态。

## 8. 已知问题、风险与限制

- **R-1 本地无 PostgreSQL**：本机未装 PG/Docker，PG 迁移只做 `alembic --sql` 离线校验，roundtrip 与双实现 PG 侧语义依赖 CI 的 PG service 容器；接手后若要在本地验 PG，先 `docker compose -f docker-compose.dev.yml up -d postgres` 并设 `DATABASE_URL`。
- **R-2 无速率限制**：登录失败 5 次锁定 15 分钟（`account.failed_attempts`/`locked_until`），但**无 IP/账号级速率限制与验证码**，暴力破解防护依赖锁定；生产环境建议前置网关限流（M3+）。
- **R-3 重放检测粒度粗**：Refresh 重放会吊销**同一账号全部会话**（非仅泄露的那条），多设备用户会被动登出；权衡安全性优先，若需细粒度需引入设备级令牌族（后续优化）。
- **R-4 令牌未绑定设备指纹强校验**：Access Token 的 `sid` 声明回查会话，但会话与设备绑定仅记录 `device_id`，未做设备指纹二次校验；M3 心跳上线后强化。
- **R-5 无密码找回/改密/注册开放**：账号由开发者端（M2 未做 UI）创建；商户自助改密、找回不在本期范围（开发者端页面随 M2-3 转入下一分支）。
- **R-6 离线宽限期仅服务端评估**：客户端离线时长由"许可证到期日 + 宽限天数"在服务端判定，本地工作台在 M3 接入前不消费该判定；当前仅 Web 端可见状态。
- **R-7 开发者端页面缺失**：`/developers` 仍为占位（M2-3 未做），开发者账号 `developer_demo` 可登录但无管理界面；创建商户/许可证的实际管理操作需下一分支补全（本期仅领域/仓储/API 层就绪）。
- **R-8 平台 CI 双绿待 PR 确认**：本批为本地命令记录（本地无 PG，云端迁移测试 skip）；PR 推送后由 PG service 容器执行云端真实校验，merge 前确认 platform-ci 全绿。

## 9. 回滚方式

- **云端迁移（PostgreSQL）**：在 `services/control-plane/` 目录执行
  ```powershell
  uv run alembic downgrade control_0003_license_product_feature   # 摘除 0004 audit_log（回 control_0003）
  uv run alembic downgrade control_0002_tenant_account_device    # 摘除 0003/0004（回 control_0002）
  uv run alembic downgrade control_0001_control_meta             # 全部回滚（control_0001）
  # 指定 DATABASE_URL：uv run alembic -x db_url=<连接串> downgrade <目标>
  ```
  注意：downgrade 删除对应表数据，执行前先备份（本地 PG 用 `pg_dump`）。
- **git 分支**：M2 无对外发布物（无 wheel/安装包）。合并前回滚 = 丢弃 `feature/a-m2-control-plane-auth`；合并后回滚 = `git revert <merge commit>`，不使用 force push。
- **memory 仓储无状态**：演示数据不落盘（进程退出即失），无需回滚。

## 10. 待办与后续计划

1. Task 11：本分支 → `master` 提 PR，PR 描述附本文件 + §6 验证结果 + 各验收场景证据；合并前确认 platform-ci / PG service 双绿（R-8）。
2. **M2-3 开发者端页面**（下一分支）：`/developers` 商户/许可证/配置管理 UI；复用已就绪的 `application/license_usecase.py` 与 `require_developer_scope`。
3. M3 端点（`/heartbeat`、`/tasks*`、`/sync/*`、`/config`、`/telemetry`、`/merchants`）实装，沿用 §7 的鉴权依赖与 SSE 事件结构。
4. 速率限制与验证码（R-2）、设备指纹强校验（R-4）、密码找回/改密（R-5）随后续里程碑。
5. 接手后可先跑 `§6.1` 七项门禁 + `§5.1` 脚本化演示，确认本地绿后再动代码。

## 11. 关键代码位置索引

**领域层** `services/control-plane/app/domain/`

- `account.py` — `AccountRole`、`MAX_FAILED_ATTEMPTS=5`、`MAX_ACTIVE_SESSIONS_PER_ACCOUNT=10`、`utc_now()`
- `session.py` — `Session`、`ClientType`、`REFRESH_TOKEN_TTL`、刷新指纹与轮换/重放语义
- `device.py` — `Device`、设备状态机与吊销终态
- `license.py` — `License` 状态机 + 离线宽限期评估（`ACTIVE`/`GRACE`/`EXPIRED`/`REVOKED`）
- `tenant.py` — `Tenant`（含 `product_profile_id`、挂起语义）
- `catalog.py` — `ProductProfile` / `FeatureGrant`
- `audit.py` — `AuditLog`、`AuditAction`、`AuditResult`

**基础设施层** `services/control-plane/app/infrastructure/`

- `auth/passwords.py` — Argon2id 哈希与校验
- `auth/tokens.py` — JWT 签发/校验、`issue_access_token` / `new_refresh_token` / `refresh_token_hash`
- `auth/scopes.py` — JWT Principal 解析（dev token 已下线）
- `auth/ids.py` — `new_id(PREFIX_*)` 标识生成
- `db/models.py`、`db/repositories.py` — PostgreSQL 实现（`DomainSession` 别名规避与 SQLAlchemy `Session` 重名）
- `memory/repositories.py`、`memory/seed.py` — 内存实现与演示种子（生产拒绝）
- `realtime/hub.py` — `EventHub`（`StatusEvent`、单调事件 ID、环形缓冲、`subscribe()`、`events_since()`、`drain()`、`latest_event_id`）

**应用层** `services/control-plane/app/application/`

- `ports.py` — Identity / Device / Entitlement / Audit 四个仓储协议
- `auth_usecase.py` — 登录 / 刷新轮换 / 注销 / me（`_new_session` / `_issue_tokens`）
- `device_usecase.py` — 设备注册（幂等 / 吊销拒绝 / max_devices 上限）
- `license_usecase.py` — 许可证评估与宽限期
- `audit.py` — 审计写入（白名单 `ALLOWED_DETAIL_KEYS`）
- `container.py` — `build_container` / `build_test_container` / `Container`（按 `CONTROL_PLANE_REPOSITORY` 选择实现）

**API 层** `services/control-plane/app/api/v1/`

- `deps.py` — `require_principal`（回查会话）/ `optional_principal` / `require_developer_scope` / `require_tenant_access` / `require_tenant_with_license`
- `routes.py` — auth/account/devices 实装；events/stream + events/snapshot；stub 501 保留
- `schemas.py` — Pydantic 请求/响应模型
- `errors.py` — `ApiError`、`register_exception_handlers`
- `main.py` — `create_app` 挂载路由
- `settings.py` — 新增 `AUTH_SECRET`、`CONTROL_PLANE_REPOSITORY`、`CONTROL_PLANE_DEMO_PASSWORD`、`LICENSE_OFFLINE_GRACE_DAYS` 等

**测试** `services/control-plane/tests/`

- `conftest.py` — 注入内存容器、`login_token`、`add_merchant_account`、`TEST_PASSWORD`
- `test_auth_flow.py`（16 项认证闭环）、`test_devices.py`、`test_license_grace.py`、`test_status_stream.py`（ASGITransport + 真实 uvicorn 冒烟）、`test_audit.py`、`test_api_stubs.py`

**Web 端** `apps/web/src/`

- `api/client.ts` — 真实 API 客户端
- `hooks/use-status-stream.ts` — SSE + 30s 轮询降级
- `app/(public)/login/page.tsx` — 登录页
- `app/(merchant)/dashboard/page.tsx` — 商户工作台
- `package.json` — 新增 `@ant-design/icons`

**迁移** `services/control-plane/alembic/versions/`

- `0002_tenant_account_device.py`、`0003_license_product_feature.py`、`0004_audit.py`

**文档/配置**

- `docs/data-dictionary.md` §2.3~~2.10 与 §4~~§5；`docs/er-diagram.md` §2.2~2.3
- `.env.example` 新增控制平面配置项
- `services/control-plane/pyproject.toml` 新增 `argon2-cffi`、`pyjwt`
- `.trae/specs/deliver-m2-control-plane-auth/{spec,tasks}.md`
