# 开发者 A：M2 控制平面认证授权与商户端状态 Spec

## Why

M1 本地业务闭环已交付（PR #3，`feature/a-m1-local-business-loop`）：本地 SQLite 主数据、库存事件、导入向导、分析运行、报告导出、备份恢复全部落地，196 passed + 1 skipped。但控制平面仍是 M0 骨架：`/api/v1` 全部端点恒 501，认证靠 dev token（`Bearer merchant` / `Bearer developer` 字符串），云端库只有 `control_meta` / `control_enum` 两张元数据表，Web 三端页面均为占位。

按 `开发需求-A平台工作台.md` §4 M2（第 4-7 周），本期需要把控制平面从"占位骨架"推进到"可登录、可授权、可追溯、可实时看状态"，让商户 Web 能真正看到自己的设备与授权状态，为 M3 的托盘 Agent（心跳/任务/同步）铺好底座。

本批次取 M2 五项中的第 1、4、5 项与第 2 项，第 3 项（开发者端页面）转入下一分支——按主基线 §26.1"功能分支保持 1 至 3 天，禁止大包"。

## What Changes

- **分支**：从 `master`（d7e1468，M1 合并后）创建 `feature/a-m2-control-plane-auth`。
- **认证实装（M2 第 1 项）**：`POST /auth/login`、`POST /auth/refresh`、`POST /auth/logout`、`GET /account/me`、`POST /devices/register`、`GET /devices`。密码 Argon2id；Access Token 15 分钟 JWT；Refresh Token 一次性轮换、只存 SHA-256 指纹、重放即吊销整条会话族。
- **许可证与离线宽限期（M2 第 1 项）**：许可证状态机 + 宽限期评估（默认 7 天，配置可覆盖），宽限期内本地功能不中断，超期返回 `LICENSE_EXPIRED`；设备注册受 `max_devices` 约束。
- **Scope 与审计（M2 第 4 项）**：dev token 下线，`require_tenant_access` / `require_developer_scope` 改为校验真实 JWT 的 scope；登录、刷新、注销、设备注册、许可证状态变更全部写 `audit_log`。
- **SSE 状态流与轮询降级（M2 第 5 项）**：`GET /events/stream`（`Last-Event-ID` 续传、15 秒 keepalive）与 `GET /events/snapshot?since=`（30 秒轮询降级入口），事件中心带环形缓冲与单调事件 ID。
- **商户端页面（M2 第 2 项）**：登录页接真实 API；商户工作台展示许可证与授权、设备列表、版本状态，任务与同步积压标注 M3 待开放。
- **持久化**：新增云端迁移 `control_0002`（tenant/account/session/device）、`control_0003`（product_profile/license/feature_grant）、`control_0004`（audit_log）。
- **仓储双实现**：定义仓储协议，提供 PostgreSQL 实现（生产路径）与内存实现（测试注入与本地无库演示）。本机无 PostgreSQL/Docker，内存实现保证认证等逻辑可 100% 本地验证；PG 迁移用 `alembic --sql` 离线校验，运行时由 CI 的 PG service 容器验证。
- **不动的部分**：`/heartbeat`、`/tasks*`、`/sync/*`、`/config`、`/telemetry`、`/merchants` 保持 stub 501（M3 范围）；contracts 包零改动（错误码只取 `contracts.enums.ErrorCode` 已冻结值）；不触碰 B 侧 `packages/warehouse-engine`、`skills/`；不改动本地库与工作台。

## Impact

- Affected specs: `deliver-m0-*` / `deliver-m1-kpi-engine`（B 侧，本 spec 不触碰）。
- Affected code: `services/control-plane/`（domain、application、infrastructure/db、infrastructure/auth、infrastructure/realtime、api/v1、alembic/versions）、`apps/web/`（login、dashboard、api client、状态流 hook）、`packages/contracts-schema/openapi.json`（重导出）、`docs/`（data-dictionary、er-diagram、m2-handover-a、DECISIONS、CHANGELOG）。
- 破坏性变更：`Bearer merchant` 这类 dev token 不再被接受，客户端必须改用登录换发的 JWT。现有 `tests/test_api_stubs.py` 同步改造为"真实登录后访问 stub 端点"。

## ADDED Requirements

### Requirement: 账号登录与令牌发放

系统 SHALL 校验登录名与 Argon2id 密码哈希，成功后发放 Access Token（JWT，15 分钟）与 Refresh Token（一次性，仅存 SHA-256 指纹）。

#### Scenario: 登录成功
- **WHEN** 提交正确账号与密码
- **THEN** 返回 200 与 `{"data": {access_token, refresh_token, expires_in, token_type, account, tenant, license}}`，其中 `license` 为许可证与宽限期评估结果

#### Scenario: 密码错误
- **WHEN** 密码不匹配
- **THEN** 返回 401 `AUTH_REQUIRED`（不区分"账号不存在"与"密码错误"，避免账号枚举），并写审计

#### Scenario: 账号停用或被锁
- **WHEN** 账号状态为 `DISABLED` 或 `LOCKED`
- **THEN** 返回 403 `AUTH_FORBIDDEN`，details 标注 `account_status`

### Requirement: 刷新令牌轮换与重放检测

系统 SHALL 在每次刷新时轮换 Refresh Token：旧令牌立即失效；已失效令牌被再次使用时，吊销同一会话族全部令牌并返回 401。

#### Scenario: 正常轮换
- **WHEN** 使用有效 Refresh Token 刷新
- **THEN** 返回新 Access/Refresh 对，旧 Refresh Token 失效（二次使用被拒）

#### Scenario: 重放检测
- **WHEN** 已轮换掉的 Refresh Token 再次被用于刷新
- **THEN** 返回 401 `AUTH_REQUIRED`，details.reason = `REFRESH_TOKEN_REUSED`，该会话族全部撤销

### Requirement: 注销

系统 SHALL 支持注销当前会话：撤销 Session 与其全部 Refresh Token，重复注销保持幂等成功。

#### Scenario: 重复注销
- **WHEN** 已注销的 Access Token 再次调用注销
- **THEN** 返回 200（幂等），不报错

### Requirement: 设备注册与许可证约束

系统 SHALL 在商户登录后注册设备：同一 `(tenant_id, fingerprint)` 只保留一台设备；已吊销设备不得复用；在册设备数达到许可证 `max_devices` 时拒绝新设备注册。

#### Scenario: 指纹重复
- **WHEN** 同一商户已存在相同 fingerprint 且未吊销的设备
- **THEN** 返回已存在设备（幂等），不新增记录

#### Scenario: 超出设备数上限
- **WHEN** 在册设备数 >= `max_devices`
- **THEN** 返回 403 `AUTH_FORBIDDEN`，details.reason = `DEVICE_LIMIT_EXCEEDED`

#### Scenario: 设备已吊销
- **WHEN** 该 fingerprint 对应设备状态为 `REVOKED`
- **THEN** 返回 403 `DEVICE_REVOKED`

### Requirement: 许可证状态与离线宽限期

系统 SHALL 按到期日与宽限期（默认 7 天，可由配置覆盖）评估许可证：`ACTIVE` / `GRACE` / `EXPIRED` / `REVOKED`；宽限期内商户可继续本地工作但状态如实上报，超期返回 `LICENSE_EXPIRED`。

#### Scenario: 宽限期内
- **WHEN** 许可证已过期但仍在宽限天数内
- **THEN** 状态为 `GRACE`，返回 `grace_ends_at` 与剩余天数，接口正常放行

#### Scenario: 宽限期外
- **WHEN** 许可证过期且已超出宽限天数
- **THEN** 商户接口返回 403 `LICENSE_EXPIRED`

### Requirement: Scope 与租户隔离

系统 SHALL 以 JWT 携带的 `tenant_id` 与 scope 决定数据范围：商户 scope 无法访问 `developer:*` 接口，禁止信任前端传入的 `merchant_id`。

#### Scenario: 商户访问开发者接口
- **WHEN** 商户 scope 令牌访问 `GET /merchants`
- **THEN** 返回 403 `AUTH_FORBIDDEN`，details.required_scope = `developer`

#### Scenario: 租户隔离
- **WHEN** 商户 A 的令牌查询设备列表
- **THEN** 只返回 `tenant_id` 等于令牌声明的设备，不含其他商户

### Requirement: 审计记录

系统 SHALL 为登录（成功/失败）、刷新、注销、设备注册、许可证状态变更写入 `audit_log`，记录 actor、租户、动作、目标、request_id 与结果。

#### Scenario: 登录失败留痕
- **WHEN** 密码错误
- **THEN** 写入一条 `result=DENIED` 的审计记录，日志不含密码原文

### Requirement: SSE 状态流与轮询降级

系统 SHALL 提供 `GET /events/stream` 的 SSE 通道（带单调事件 ID、`Last-Event-ID` 断线续传、15 秒 keepalive），并提供 `GET /events/snapshot?since=` 供客户端降级为 30 秒轮询。

#### Scenario: 断线续传
- **WHEN** 客户端携带 `Last-Event-ID` 重连
- **THEN** 服务端先补发该 ID 之后的缓冲事件，再推送当前快照

#### Scenario: 降级轮询
- **WHEN** SSE 连接失败或连续异常
- **THEN** Web 切换为每 30 秒调用 `/events/snapshot`，界面明示"轮询降级"

### Requirement: 仓储协议与双实现

系统 SHALL 以仓储协议隔离持久化：PostgreSQL 实现为生产路径，内存实现用于测试注入与本地无库演示，两者对同一用例语义一致。

#### Scenario: 实现一致性
- **WHEN** 同一组用例分别跑在内存仓储与 PG 仓储上
- **THEN** 行为与返回一致（PG 侧由 CI 的 service 容器验证）

## MODIFIED Requirements

### Requirement: dev token 鉴权（M0 临时行为）

M0 以 `Bearer merchant` 字符串冒充令牌，仅用于打通 Scope 依赖。M2 起改为校验真实 JWT；令牌缺失/无效 → 401 `AUTH_REQUIRED`，scope 不足 → 403 `AUTH_FORBIDDEN`。
**Migration**: 客户端改用 `/auth/login` 换发令牌；`tests/test_api_stubs.py` 改为登录后访问 stub 端点，端点清单与 501 断言不变。

### Requirement: stub 端点

`/heartbeat`、`/tasks`、`/tasks/pull`、`/sync/events/pull`、`/sync/ack`、`/config`、`/telemetry`、`/merchants` 维持 501 stub（M3 交付），保留 `details.stub` 标注。
**Migration**: 这些端点的鉴权依赖从 dev token 改为真实 JWT，测试需携带登录后令牌。

## REMOVED Requirements

无。
