# 仓库品类分析决策工具 —— 前后端接口文档

| 项 | 值 |
|---|---|
| 文档版本 | **1.0** |
| 更新时间 | **2026-08-31** |
| 对应代码基线 | `master` @ `5f66454`（PR #12 合并后） |
| OpenAPI 版本 | `3.1.0` |
| API 版本 | `0.1.0`（`services/control-plane/app/settings.py::APP_VERSION`） |
| 契约 Schema 版本 | `1.0`（`contracts.schema_version`） |
| 快照文件 | `packages/contracts-schema/openapi.json`（**唯一来源**，由 `scripts/export_openapi.py` 导出，CI 校验防漂移） |

> **重要架构事实**：分析能力（`warehouse-engine`）目前是**纯进程内 Python API，无 HTTP 暴露**。
> `openapi.json` 中不存在任何 `/analyze`、`/capabilities` 路径。进程内接口见 **第七章**。

---

## 一、通用约定

### 1.1 基础信息

| 项 | 值 |
|---|---|
| 服务名 | `warehouse-control-plane` |
| 默认基地址（本地） | `http://localhost:8000` |
| 环境变量 | `WORKBENCH_API_URL`（桌面端）、`NEXT_PUBLIC_API_BASE_URL`（Web 端） |
| 内容类型 | `application/json; charset=utf-8` |
| 字符编码 | UTF-8 |
| 时间格式 | RFC 3339 / ISO 8601，UTC（如 `2026-08-31T08:01:00Z`） |
| 日期格式 | `YYYY-MM-DD` |
| 金额与数量 | **字符串传输**（Decimal 语义），禁止 float |

### 1.2 路由前缀

| 前缀 | 说明 |
|---|---|
| 无 | `GET /health` 直挂根路径 |
| `/api/v1` | 其余全部业务接口 |

### 1.3 统一成功响应信封

所有 2xx 响应统一为 `{"data": ...}` 信封，逐个端点显式声明（不使用泛型）。

```json
{ "data": { /* 端点专属载荷 */ } }
```

### 1.4 统一错误响应结构

**所有非 2xx 响应统一结构**：

```json
{
  "error": {
    "code": "AUTH_FORBIDDEN",
    "message": "缺少 merchant Scope，无权访问该接口。",
    "details": { "required_scope": "merchant" },
    "request_id": "6f1c2b3a4d5e6f708192a3b4c5d6e7f8"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `error.code` | string | ✅ | 错误码（见 §1.6） |
| `error.message` | string | ✅ | 中文文案，**仅用于展示**；客户端只按 `code` 判断行为，不解析文案 |
| `error.details` | object | ✅ | 结构化补充信息，键不固定 |
| `error.request_id` | string | ✅ | 32 位十六进制，与响应头 `X-Request-ID` 一致 |

### 1.5 响应头

| 头 | 说明 |
|---|---|
| `X-Request-ID` | 每个响应必带，32 位十六进制（`uuid4().hex`），用于串联审计日志 |
| `Content-Type` | `application/json`；SSE 端点为 `text/event-stream` |

### 1.6 统一错误码表

`ErrorCode` 共 **18 个**成员（`packages/contracts-python/src/contracts/enums.py`）。

| # | 错误码 | HTTP | 含义 | 当前是否由控制平面产生 | 触发场景 |
|---|---|---|---|---|---|
| 1 | `AUTH_REQUIRED` | **401** | 未登录或凭证无效 | ✅ | 令牌缺失/过期/签名错误；会话已撤销；账号或密码错误；刷新令牌无效或重放；账号不存在或已删除 |
| 2 | `AUTH_FORBIDDEN` | **403** | 无权限 | ✅ | Scope 不足；账号锁定/停用；商户已暂停；设备数达上限 |
| 3 | `LICENSE_EXPIRED` | **403** | 许可证过期或不可用 | ✅ | 许可证状态为 `EXPIRED`/`REVOKED`/`MISSING` |
| 4 | `DEVICE_REVOKED` | **403** | 设备已被吊销 | ✅ | 同指纹设备状态为 `REVOKED` |
| 5 | `DATA_VALIDATION_FAILED` | **400** | 数据校验失败 | ✅ | 请求体 Pydantic 校验失败（**覆盖 FastAPI 默认 422**）；引擎侧精度/冲销引用/盘点数量/原始载荷解析失败 |
| 6 | `SKU_NOT_FOUND` | **400** ⚠️ | 引用了不存在的 SKU | 引擎侧 | 流水引用了不在主数据中的 SKU |
| 7 | `DUPLICATE_EVENT` | **409** | 重复库存事件 | 引擎侧 | 同一 `event_id` 出现多次 |
| 8 | `INTERNAL_ERROR` | **500** | 服务内部错误 | ✅ | 兜底异常处理器；**stub 端点复用此码返回 501** |
| 9 | `CONFIG_VERSION_UNSUPPORTED` | 400 ⚠️ | 配置版本不支持 | ❌ | M3 预留 |
| 10 | `CONFIG_SIGNATURE_INVALID` | 400 ⚠️ | 配置签名校验失败 | ❌ | M3 预留 |
| 11 | `INVENTORY_INSUFFICIENT` | 400 ⚠️ | 库存不足 | ❌ | 引擎侧未使用（负库存走 Warning） |
| 12 | `SYNC_CURSOR_INVALID` | 400 ⚠️ | 同步游标无效 | ❌ | M3 预留 |
| 13 | `SYNC_ENVELOPE_EXPIRED` | 400 ⚠️ | 同步信封过期 | ❌ | M3 预留 |
| 14 | `ANALYSIS_CANCELLED` | 400 ⚠️ | 分析被取消 | ❌ | 引擎侧 `AnalysisCancelledError` 默认码，但**代码中从未抛出** |
| 15 | `ANALYSIS_FAILED` | 400 ⚠️ | 分析失败 | ❌ | 预留 |
| 16 | `REPORT_RENDER_FAILED` | 400 ⚠️ | 报告渲染失败 | ❌ | 预留 |
| 17 | `BACKUP_VERIFY_FAILED` | 400 ⚠️ | 备份校验失败 | ❌ | 预留 |
| 18 | `MIGRATION_FAILED` | 400 ⚠️ | 数据迁移失败 | ❌ | 预留 |
| 19 | `BENCHMARK_UNAVAILABLE` | 400 ⚠️ | 无匹配行业基准 | ❌ | 基准计算器为存根 |

> ⚠️ **已知不一致**：控制平面的 `ERROR_STATUS` 映射表**只显式声明了 7 个**错误码 → HTTP 状态的映射；上表标「400 ⚠️」的 12 个错误码若被抛出，会落到**默认 400**。
> 显式映射如下：
> ```python
> ERROR_STATUS = {AUTH_REQUIRED: 401, AUTH_FORBIDDEN: 403, LICENSE_EXPIRED: 403,
>                 DEVICE_REVOKED: 403, DATA_VALIDATION_FAILED: 400,
>                 DUPLICATE_EVENT: 409, INTERNAL_ERROR: 500}
> DEFAULT_ERROR_STATUS = 400
> ```

### 1.7 全局异常处理器

| 触发 | HTTP | `code` | `message` | `details` |
|---|---|---|---|---|
| 应用层 `ControlPlaneError` | `ERROR_STATUS.get(code, 400)` | 异常携带的 code | 异常携带的 message | 异常携带的 details |
| API 层 `ApiError` | 异常声明的 `status_code` | 异常携带的 code | 异常携带的 message | 异常携带的 details |
| `RequestValidationError`（请求体校验失败） | **400** | `DATA_VALIDATION_FAILED` | `请求数据校验失败。` | Pydantic 错误明细数组 |
| 兜底 `Exception` | 500 | `INTERNAL_ERROR` | `服务内部错误。` | `{}`（**不回传堆栈**） |

---

## 二、鉴权

### 2.1 方式

**Bearer JWT（HS256）**，请求头：`Authorization: Bearer <access_token>`

### 2.2 令牌

| 令牌 | 类型 | 有效期 | 存储 |
|---|---|---|---|
| Access Token | JWT HS256 | **15 分钟**（`ACCESS_TOKEN_TTL`） | 客户端自管（Web 端存 localStorage；桌面端存 `auth.json`） |
| Refresh Token | 不透明随机串 | **30 天**（`REFRESH_TOKEN_TTL`），每次轮换重置 | 同上；服务端只存 SHA-256 指纹 |

JWT claims：`account_id`、`tenant_id`、`role`、`scopes`、`session_id`、`device_id`。

### 2.3 鉴权依赖链（四层递进）

```text
Authorization: Bearer <JWT>
  │
  ├─(1) optional_principal   仅解析，不抛错；失败返回 None
  ├─(2) require_valid_token  校验签名与过期（不查会话）→ 失败 401
  ├─(3) require_principal    再回查会话 revoked_at     → 已撤销 401（★ 注销立即生效）
  ├─(4) scope 校验           不足 → 403，details={"required_scope": "<scope>"}
  └─(5) require_tenant_with_license  额外做许可证放行判定 → 403 LICENSE_EXPIRED
```

| 依赖函数 | 校验内容 | 用于 |
|---|---|---|
| `optional_principal` | 仅解析 | 内部 |
| `require_valid_token` | 令牌签名 + 过期 | `POST /auth/logout` |
| `require_principal` | 令牌 + 会话未撤销 | `GET /account/me` |
| `require_tenant_access` | `require_principal` + scope `merchant` | 事件流、配置、任务、同步、心跳 |
| `require_developer_scope` | `require_principal` + scope `developer` | 商户列表、技术日志 |
| `require_tenant_with_license` | `require_tenant_access` + 许可证放行 | 设备注册、设备列表 |

### 2.4 Scope

| Scope | 角色 | 可访问接口 |
|---|---|---|
| `merchant` | `MERCHANT_OWNER` | `/account/me`、`/devices*`、`/events/*`、`/config`、`/tasks*`、`/sync/*`、`/heartbeat` |
| `developer` | `DEVELOPER` | `/merchants`、`/telemetry` |

> **租户隔离**：数据范围由令牌中的 `tenant_id` 决定，**不信任前端传入的 merchant_id**。
> **设备绑定**：`Principal.device_id` 存在于令牌中，但**鉴权依赖不做设备比对校验**。

### 2.5 客户端约定

- M0 的 `Bearer merchant` 明文 dev token **已下线**，必须走 `/auth/login` 换发；
- Access Token 过期时用 Refresh Token 轮换；**检测到 401 `AUTH_REQUIRED` 且 `details.reason=REFRESH_TOKEN_REUSED` 时必须清空本地令牌并重新登录**（该账号全部会话已被服务端撤销）；
- 刷新成功后必须替换本地保存的 Refresh Token（服务端已轮换，旧令牌立即失效）。

---

## 三、接口清单

| # | 方法 | 路径 | 说明 | 鉴权 | 状态 |
|---|---|---|---|---|---|
| 1 | GET | `/health` | 健康检查 | 无 | ✅ 已实现 |
| 2 | POST | `/api/v1/auth/login` | 账号登录 | 无 | ✅ 已实现 |
| 3 | POST | `/api/v1/auth/refresh` | 刷新令牌 | 无（凭 refresh_token） | ✅ 已实现 |
| 4 | POST | `/api/v1/auth/logout` | 注销 | 有效令牌 | ✅ 已实现 |
| 5 | GET | `/api/v1/account/me` | 当前账号上下文 | 令牌 + 会话未撤销 | ✅ 已实现 |
| 6 | GET | `/api/v1/devices` | 设备列表 | `merchant` + 许可证 | ✅ 已实现 |
| 7 | POST | `/api/v1/devices/register` | 设备注册 | `merchant` + 许可证 | ✅ 已实现 |
| 8 | GET | `/api/v1/events/snapshot` | 状态快照 | `merchant` | ✅ 已实现 |
| 9 | GET | `/api/v1/events/stream` | 状态流（SSE） | `merchant` | ✅ 已实现 |
| 10 | GET | `/api/v1/config` | 配置下发 | `merchant` | ⏳ stub（恒 501） |
| 11 | POST | `/api/v1/heartbeat` | 设备心跳 | `merchant` | ⏳ stub（恒 501） |
| 12 | GET | `/api/v1/tasks` | 任务列表 | `merchant` | ⏳ stub（恒 501） |
| 13 | POST | `/api/v1/tasks/pull` | 拉取任务 | `merchant` | ⏳ stub（恒 501） |
| 14 | GET | `/api/v1/sync/events/pull` | 拉取同步事件 | `merchant` | ⏳ stub（恒 501） |
| 15 | POST | `/api/v1/sync/ack` | 同步确认 | `merchant` | ⏳ stub（恒 501） |
| 16 | GET | `/api/v1/telemetry` | 技术日志 | `developer` | ⏳ stub（恒 501） |
| 17 | GET | `/api/v1/merchants` | 商户列表 | `developer` | ⏳ stub（恒 501） |

---

## 四、接口详情

### 4.1 `GET /health` —— 健康检查

| 项 | 值 |
|---|---|
| 方法 / 路径 | `GET /health`（**无 `/api/v1` 前缀**） |
| 鉴权 | 无 |
| Content-Type | `application/json` |
| 状态码 | 恒 `200` |
| 说明 | 路由为**同步函数**（在线程池执行），数据库探测不阻塞事件循环 |

**请求参数**：无

**响应 `HealthResponse`**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | string | ✅ | 恒 `"ok"` |
| `app_version` | string | ✅ | 应用版本，默认 `"0.1.0"` |
| `database` | string | ✅ | `"up"` / `"down"` |

> 数据库不可达时**仍返回 200**，`status` 保持 `ok`，`database` 如实为 `down`（不伪报）。

**示例**

```bash
curl -s http://localhost:8000/health
```

```json
{ "status": "ok", "app_version": "0.1.0", "database": "up" }
```

---

### 4.2 `POST /api/v1/auth/login` —— 账号登录

| 项 | 值 |
|---|---|
| 方法 / 路径 | `POST /api/v1/auth/login` |
| 鉴权 | **无** |
| operationId | `login_api_v1_auth_login_post` |
| 状态码 | `200` / `401` / `403` |

**请求体 `LoginRequest`**

| 字段 | 类型 | 必填 | 默认值 | 示例 | 说明 |
|---|---|---|---|---|---|
| `username` | string | ✅ | — | `"demo_merchant"` | 登录名 |
| `password` | string | ✅ | — | `"******"` | 密码 |
| `client_type` | string | ❌ | `"WEB"` | `"DESKTOP"` | `DESKTOP` / `WEB` / `MINI_PROGRAM`；未知值回退 `WEB` |
| `device_id` | string \| null | ❌ | `null` | `"dev_ab12..."` | 设备标识 |

**响应 `LoginResponse`** → `data: AuthData`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `tokens` | `TokenData` | ✅ | 令牌对 |
| `account` | `AccountData` | ✅ | 账号信息（**不含凭证字段**） |
| `tenant` | `TenantData` \| null | ✅ | 商户；开发者账号为 `null` |
| `license` | `LicenseData` | ✅ | 许可证评估（**登录不阻断许可证**） |

`TokenData`：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `access_token` | string | ✅ | JWT |
| `refresh_token` | string | ✅ | **明文仅此一次出现** |
| `token_type` | string | ✅ | 恒 `"Bearer"` |
| `expires_in` | integer | ✅ | Access Token 剩余秒数 |
| `refresh_expires_at` | string(date-time) | ✅ | Refresh Token 到期时间 |

`AccountData`：`account_id`(✅) / `login_name`(✅) / `role`(✅) / `status`(✅) / `tenant_id`(string\|null) / `last_login_at`(string(date-time)\|null)

`TenantData`：`tenant_id`(✅) / `name`(✅) / `status`(✅) / `product_profile_id`(string\|null)

`LicenseData`：见 §4.5 公共结构

**错误**

| HTTP | code | message | details | 触发 |
|---|---|---|---|---|
| 401 | `AUTH_REQUIRED` | `账号或密码错误。` | `{}` | 账号不存在（审计 `reason=ACCOUNT_NOT_FOUND`）或密码错误（`reason=BAD_CREDENTIALS`） |
| 403 | `AUTH_FORBIDDEN` | `账号处于锁定或停用状态，无法登录。` | `{"reason":"ACCOUNT_UNAVAILABLE","account_status":"LOCKED"}` | `DISABLED` 或锁定期内 |
| 403 | `AUTH_FORBIDDEN` | `商户已暂停服务，请联系服务商。` | `{"reason":"TENANT_SUSPENDED","tenant_id":"tnt_..."}` | 商户 `SUSPENDED` |

> **反账号枚举**：账号不存在时不执行密码校验，两类失败返回同一文案。

**示例**

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo_merchant","password":"<pwd>","client_type":"DESKTOP"}'
```

```json
{
  "data": {
    "tokens": {
      "access_token": "eyJhbGciOiJIUzI1NiIs...",
      "refresh_token": "kR8x...",
      "token_type": "Bearer",
      "expires_in": 900,
      "refresh_expires_at": "2026-09-30T08:46:00Z"
    },
    "account": {
      "account_id": "acc_1f0c...", "login_name": "demo_merchant",
      "role": "MERCHANT_OWNER", "tenant_id": "tnt_9a2b...",
      "status": "ACTIVE", "last_login_at": "2026-08-31T08:31:00Z"
    },
    "tenant": { "tenant_id": "tnt_9a2b...", "name": "示例商贸", "status": "ACTIVE", "product_profile_id": null },
    "license": { "status": "ACTIVE", "license_id": "lic_3c4d...", "product_profile_code": null,
                 "starts_at": "2026-01-01", "expires_at": "2027-01-01", "grace_days": 7,
                 "grace_ends_at": null, "days_remaining": 123, "max_devices": 3, "features": [] }
  }
}
```

---

### 4.3 `POST /api/v1/auth/refresh` —— 刷新令牌

> **重放即撤销该账号全部会话。**

| 项 | 值 |
|---|---|
| 方法 / 路径 | `POST /api/v1/auth/refresh` |
| 鉴权 | 无（凭 `refresh_token` 本身） |
| 状态码 | `200` / `401` |

**请求体 `RefreshRequest`**

| 字段 | 类型 | 必填 | 示例 | 说明 |
|---|---|---|---|---|
| `refresh_token` | string | ✅ | `"kR8x..."` | 上一次登录/轮换返回的 refresh_token |

**响应**：与登录同构（`RefreshResponse` → `data: AuthData`）

**错误**

| HTTP | code | message | details | 触发 |
|---|---|---|---|---|
| 401 | `AUTH_REQUIRED` | `会话已失效，请重新登录。` | `{"reason":"SESSION_INACTIVE"}` | 命中当前指纹但会话已撤销或已过期 |
| 401 | `AUTH_REQUIRED` | `检测到刷新令牌被重复使用，已撤销全部会话，请重新登录。` | `{"reason":"REFRESH_TOKEN_REUSED","revoked_sessions":3}` | 命中 `previous_refresh_token_hash` |
| 401 | `AUTH_REQUIRED` | `刷新令牌无效。` | `{}` | 两个指纹均未命中（**不写审计**） |

**示例**

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/refresh \
  -H 'Content-Type: application/json' \
  -d '{"refresh_token":"kR8x..."}'
```

---

### 4.4 `POST /api/v1/auth/logout` —— 注销

| 项 | 值 |
|---|---|
| 方法 / 路径 | `POST /api/v1/auth/logout` |
| 鉴权 | `require_valid_token`（**只验令牌，不查会话撤销**） |
| 请求体 | **无** |
| 状态码 | `200` / `401` |

**响应 `LogoutResponse`** → `data: LogoutData`

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `session_id` | string | ✅ | — | 被撤销的会话标识 |
| `revoked` | boolean | ❌ | `true` | 恒为 `true` |

> **幂等**：重复注销恒返回 200（已撤销的会话也返回 200），避免用户在令牌未过期期间重复点击注销收到 401。

**示例**

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/logout \
  -H "Authorization: Bearer <access_token>"
```

```json
{ "data": { "session_id": "ses_7d8e...", "revoked": true } }
```

---

### 4.5 `GET /api/v1/account/me` —— 当前账号上下文

| 项 | 值 |
|---|---|
| 方法 / 路径 | `GET /api/v1/account/me` |
| 鉴权 | `require_principal` |
| 请求参数 | 无 |
| 状态码 | `200` / `401` |

**响应 `AccountMeResponse`** → `data: AccountMeData`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `account` | `AccountData` | ✅ | 账号信息 |
| `tenant` | `TenantData` \| null | ✅ | 商户（开发者账号为 `null`） |
| `license` | `LicenseData` | ✅ | 许可证评估 |

**`LicenseData` 公共结构**（登录/刷新/账号上下文/状态快照共用）

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `status` | string | ✅ | — | `ACTIVE` / `GRACE` / `EXPIRED` / `REVOKED` / `MISSING` |
| `license_id` | string \| null | ❌ | `null` | 许可证标识 |
| `product_profile_code` | string \| null | ❌ | `null` | 行业类型编码 |
| `starts_at` | string(date) \| null | ❌ | `null` | 生效日 |
| `expires_at` | string(date) \| null | ❌ | `null` | 到期日 |
| `grace_days` | integer | ❌ | `0` | 离线宽限天数（来自运行配置 `LICENSE_OFFLINE_GRACE_DAYS`） |
| `grace_ends_at` | string(date) \| null | ❌ | `null` | 宽限期截止日 |
| `days_remaining` | integer \| null | ❌ | `null` | 剩余天数，负数表示已过期天数 |
| `max_devices` | integer | ❌ | `0` | 设备数上限 |
| `features` | string[] | ❌ | `[]` | 已启用的功能授权编码列表 |

**`status` 语义与放行策略**

| 状态 | 含义 | 是否放行 |
|---|---|---|
| `ACTIVE` | 有效期内 | ✅ |
| `GRACE` | 已过期但在离线宽限期内 | ✅（**UI 必须明示剩余天数**） |
| `EXPIRED` | 已过期且超宽限期 | ❌ |
| `REVOKED` | 已吊销 | ❌ |
| `MISSING` | 商户无有效许可证 | ❌ |

> **关键设计**：本端点**不挂** `require_tenant_with_license`，许可证过期时仍放行，保证用户能看到「已过期」状态而不是被登录流程挡在外面。

**错误**

| HTTP | code | message | 触发 |
|---|---|---|---|
| 401 | `AUTH_REQUIRED` | `缺少或无效的凭证。` / `会话已撤销，请重新登录。` | 令牌无效 / 会话已撤销 |
| 401 | `AUTH_REQUIRED` | `账号不存在或已删除。` | 令牌中的 account 已不存在 |

**示例**

```bash
curl -s http://localhost:8000/api/v1/account/me -H "Authorization: Bearer <access_token>"
```

---

### 4.6 `POST /api/v1/devices/register` —— 设备注册

| 项 | 值 |
|---|---|
| 方法 / 路径 | `POST /api/v1/devices/register` |
| 鉴权 | `require_tenant_with_license`（`merchant` Scope + 许可证放行） |
| 状态码 | `200` / `401` / `403` |

**请求体 `DeviceRegisterRequest`**

| 字段 | 类型 | 必填 | 默认值 | 示例 | 说明 |
|---|---|---|---|---|---|
| `name` | string | ✅ | — | `"仓库办公室-PC"` | 设备名称 |
| `fingerprint` | string | ✅ | — | `"a1b2c3d4..."` | 设备指纹，`(tenant_id, fingerprint)` 唯一 |
| `device_type` | string | ❌ | `"DESKTOP"` | `"DESKTOP"` | `DESKTOP` / `WEB` / `MINI_PROGRAM`；未知值回退 `DESKTOP` |
| `app_version` | string \| null | ❌ | `null` | `"0.1.0"` | 客户端版本 |

**响应 `DeviceRegisterResponse`** → `data: DeviceData`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `device_id` | string | ✅ | 设备标识 |
| `tenant_id` | string | ✅ | 所属商户 |
| `name` | string | ✅ | 设备名称 |
| `device_type` | string | ✅ | 设备类型 |
| `fingerprint` | string | ✅ | 设备指纹 |
| `status` | string | ✅ | 新建时恒为 `REGISTERED` |
| `app_version` | string \| null | ❌ | 客户端版本 |
| `last_seen_at` | string(date-time) \| null | ❌ | 最近心跳（**M2 心跳为 stub，恒为 null**） |
| `registered_at` | string(date-time) \| null | ❌ | 注册时间 |

**业务规则**

| 场景 | 结果 |
|---|---|
| 同 `(tenant_id, fingerprint)` 已存在且未吊销 | **幂等**，返回已有设备（同一 `device_id`），刷新 `name`/`app_version`/`updated_at` |
| 同指纹设备状态为 `REVOKED` | 403 `DEVICE_REVOKED` |
| 在册（未吊销）设备数 ≥ `max_devices` | 403 `AUTH_FORBIDDEN`，`details={"reason":"DEVICE_LIMIT_EXCEEDED","max_devices":N,"registered_devices":M}` |

**错误**

| HTTP | code | message | details |
|---|---|---|---|
| 401 | `AUTH_REQUIRED` | `缺少或无效的凭证。` / `会话已撤销，请重新登录。` | — |
| 403 | `AUTH_FORBIDDEN` | `缺少 merchant Scope，无权访问该接口。` | `{"required_scope":"merchant"}` |
| 403 | `LICENSE_EXPIRED` | `许可证已被吊销，请联系服务商。` / `商户未开通有效许可证。` / `许可证已超出离线宽限期，请续期后继续使用。` | `{"status":"EXPIRED","reason":"LICENSE_GRACE_EXCEEDED","license_id":...,"expires_at":...,"grace_ends_at":...}` |
| 403 | `DEVICE_REVOKED` | `设备已被吊销，重新绑定前不可使用。` | `{"device_id":...}` |

**示例**

```bash
curl -s -X POST http://localhost:8000/api/v1/devices/register \
  -H "Authorization: Bearer <access_token>" \
  -H 'Content-Type: application/json' \
  -d '{"name":"仓库办公室-PC","fingerprint":"a1b2c3d4e5f6","device_type":"DESKTOP","app_version":"0.1.0"}'
```

```json
{
  "data": {
    "device_id": "dev_5e6f...", "tenant_id": "tnt_9a2b...",
    "name": "仓库办公室-PC", "device_type": "DESKTOP",
    "fingerprint": "a1b2c3d4e5f6", "status": "REGISTERED",
    "app_version": "0.1.0", "last_seen_at": null,
    "registered_at": "2026-08-31T08:46:00Z"
  }
}
```

---

### 4.7 `GET /api/v1/devices` —— 设备列表

| 项 | 值 |
|---|---|
| 方法 / 路径 | `GET /api/v1/devices` |
| 鉴权 | `require_tenant_with_license` |
| 请求参数 | **无**（无分页、无筛选） |
| 状态码 | `200` / `401` / `403` |

**响应 `DeviceListResponse`** → `data: DeviceData[]`

返回当前商户**全部**设备，**含已吊销**（UI 需明示吊销状态）。
PostgreSQL 实现按 `(registered_at, device_id)` 升序；内存实现为字典遍历顺序（**不保证排序**）。

**示例**

```bash
curl -s http://localhost:8000/api/v1/devices -H "Authorization: Bearer <access_token>"
```

---

### 4.8 `GET /api/v1/events/stream` —— 状态流（SSE）

| 项 | 值 |
|---|---|
| 方法 / 路径 | `GET /api/v1/events/stream` |
| 鉴权 | `require_tenant_access`（`merchant` Scope） |
| Content-Type | `text/event-stream` |
| 状态码 | `200` / `401` / `403` |

> 首帧快照 → 断线续传补发 → 实时事件 + 15 秒保活。连接失败时降级为 30 秒轮询 `GET /api/v1/events/snapshot`（DECISIONS.md D-006）。

**请求参数**

| 位置 | 名称 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| header | `Last-Event-ID` | string \| null | ❌ | 浏览器断线重连时**自动携带** |
| query | `last_event_id` | string \| null | ❌ | 首次连接指定起点 |

优先级：`last_event_id or last_event_id_query`；非法值或非正数 → `None`（从头消费）。

**响应头**

| 头 | 值 |
|---|---|
| `Content-Type` | `text/event-stream` |
| `Cache-Control` | `no-cache` |
| `Connection` | `keep-alive` |
| `X-Polling-Fallback` | `/api/v1/events/snapshot` |
| `X-Polling-Interval` | `30` |

**帧格式**

```text
id: <event_id>
event: <event_type>
data: <JSON 载荷>

```

保活帧（每 15 秒空闲）：

```text
: keepalive

```

**服务端机制**

| 项 | 值 |
|---|---|
| 环形缓冲 | 最近 **200** 条事件（`DEFAULT_BUFFER_SIZE`），用于断线续传补发 |
| 订阅者队列上限 | **100** 条（`SUBSCRIBER_QUEUE_SIZE`）；慢消费者自动丢弃最旧事件，不阻塞发布方 |
| 事件 ID | 单调递增整数，从 1 开始 |
| 按租户过滤 | ✅ 只推送本租户事件 |
| 已知事件类型 | `auth.login`（登录成功时发布） |

**示例**

```bash
curl -N http://localhost:8000/api/v1/events/stream \
  -H "Authorization: Bearer <access_token>" \
  -H "Accept: text/event-stream"
```

```text
id: 1
event: auth.login
data: {"account_id":"acc_1f0c...","client_type":"DESKTOP"}

: keepalive

```

---

### 4.9 `GET /api/v1/events/snapshot` —— 状态快照（轮询降级入口）

| 项 | 值 |
|---|---|
| 方法 / 路径 | `GET /api/v1/events/snapshot` |
| 鉴权 | `require_tenant_access` |
| 请求参数 | 无 |
| 状态码 | `200` / `401` / `403` |

**响应 `SnapshotResponse`** → `data: SnapshotData`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `event_id` | integer | ✅ | 当前最新事件 ID（尚无事件时为 `0`），可作为 SSE 续传起点 |
| `generated_at` | string(date-time) | ✅ | 快照生成时间（UTC） |
| `license` | `LicenseData` | ✅ | 许可证评估 |
| `devices` | `DeviceData[]` | ✅ | 设备列表；`tenant_id` 为空时为 `[]` |
| `pending_sync_count` | integer \| null | ❌ | **硬编码 `null`**（M3） |
| `tasks` | object[] | ❌ | **硬编码 `[]`**（M3） |

> SSE 首帧与轮询降级**共用同一结构**。

**示例**

```bash
curl -s http://localhost:8000/api/v1/events/snapshot -H "Authorization: Bearer <access_token>"
```

```json
{
  "data": {
    "event_id": 12,
    "generated_at": "2026-08-31T08:50:00Z",
    "license": { "status": "ACTIVE", "days_remaining": 123, "max_devices": 3, "features": [] },
    "devices": [ { "device_id": "dev_5e6f...", "status": "REGISTERED" } ],
    "pending_sync_count": null,
    "tasks": []
  }
}
```

---

### 4.10 Stub 端点（恒 501）

以下 8 个端点当前**一律返回 501**，用于冻结契约、供客户端先行对接。

| 方法 | 路径 | 鉴权 | operationId |
|---|---|---|---|
| GET | `/api/v1/config` | `merchant` | `get_config_api_v1_config_get` |
| POST | `/api/v1/heartbeat` | `merchant` | `heartbeat_api_v1_heartbeat_post` |
| GET | `/api/v1/tasks` | `merchant` | `list_tasks_api_v1_tasks_get` |
| POST | `/api/v1/tasks/pull` | `merchant` | `pull_tasks_api_v1_tasks_pull_post` |
| GET | `/api/v1/sync/events/pull` | `merchant` | `pull_sync_events_api_v1_sync_events_pull_get` |
| POST | `/api/v1/sync/ack` | `merchant` | `ack_sync_api_v1_sync_ack_post` |
| GET | `/api/v1/telemetry` | `developer` | `get_telemetry_api_v1_telemetry_get` |
| GET | `/api/v1/merchants` | `developer` | `list_merchants_api_v1_merchants_get` |

**统一 501 响应**（`not_implemented()`）

```json
{
  "error": {
    "code": "INTERNAL_ERROR",
    "message": "GET /api/v1/config 尚未在 M0 实现。",
    "details": { "stub": true, "endpoint": "GET /api/v1/config" },
    "request_id": "..."
  }
}
```

| 字段 | 说明 |
|---|---|
| `error.code` | ⚠️ 复用 `INTERNAL_ERROR`（枚举中无 `NOT_IMPLEMENTED`） |
| `error.details.stub` | 恒 `true`，**客户端据此判定为未实现而非真实服务端错误** |
| `error.details.endpoint` | 形如 `"{METHOD} {path}"` |

**响应码集合**：`401` / `403` / `501`；带请求体的端点另有 `400`（请求体校验失败，覆盖默认 422）。

---

## 五、数据模型（Components）

### 5.1 Schema 清单（24 个）

| Schema | required | 说明 |
|---|---|---|
| `HealthResponse` | `status`, `app_version`, `database` | 健康检查 |
| `LoginRequest` | `username`, `password` | 登录请求 |
| `RefreshRequest` | `refresh_token` | 刷新请求 |
| `DeviceRegisterRequest` | `name`, `fingerprint` | 设备注册请求 |
| `TokenData` | 全部 5 项 | 令牌对 |
| `AccountData` | `account_id`, `login_name`, `role`, `status` | 账号信息 |
| `TenantData` | `tenant_id`, `name`, `status` | 商户信息 |
| `LicenseData` | `status` | 许可证评估 |
| `DeviceData` | `device_id`, `tenant_id`, `name`, `device_type`, `fingerprint`, `status` | 设备信息 |
| `AuthData` | `tokens`, `account`, `license` | 登录/刷新载荷 |
| `AccountMeData` | `account`, `license` | 账号上下文 |
| `SnapshotData` | `event_id`, `generated_at`, `license`, `devices` | 状态快照 |
| `LogoutData` | `session_id` | 注销结果 |
| `LoginResponse` / `RefreshResponse` / `DeviceRegisterResponse` / `DeviceListResponse` / `SnapshotResponse` / `AccountMeResponse` / `LogoutResponse` | `data` | 各端点信封 |
| `ErrorBody` | `code`, `message`, `request_id` | 统一错误体内层 |
| `ErrorResponse` | `error` | 统一错误信封 |
| `HTTPValidationError` | — | `detail: ValidationError[]` |
| `ValidationError` | `loc`, `msg`, `type` | 单条校验错误 |

### 5.2 契约层数据模型（引擎侧，非 HTTP 传输）

定义在 `packages/contracts-python/src/contracts/analysis.py`，落盘 JSON Schema 于 `packages/contracts-schema/`。

| Schema 文件 | 对应模型 | required |
|---|---|---|
| `analysis-request.schema.json` | `AnalysisRequest` | `run_id`, `start_date`, `end_date`, `warehouse_ids` |
| `analysis-result.schema.json` | `AnalysisResult` | `run_id`, `engine_version`, `formula_version`, `period_start`, `period_end`, `summary`, `input_summary` |
| `sync-envelope.schema.json` | 小程序同步信封（`additionalProperties: false`，10 字段全必填） | 全部 |

> ⚠️ `analysis-request.schema.json` **只描述 `AnalysisRequest`，不含 `EngineDataset`**。数据集的 JSON Schema 由 `EngineDataset.model_json_schema()` 运行时导出，**磁盘上无 `engine-dataset.schema.json` 文件**。
> ⚠️ 两个分析 Schema 均**无 `$id` 也无 `$schema` 声明**（只有 `title`/`type`/`properties`/`required`/`$defs`）。

---

## 六、客户端调用示例

### 6.1 TypeScript（Web 端，`apps/web/src/api/client.ts`）

```ts
import { authApi, devicesApi, eventsApi, ApiRequestError, setTokens, getRefreshToken } from "@/api/client";

// 1) 登录
const data = await authApi.login({ username, password, client_type: "WEB" });
setTokens(data.tokens.access_token, data.tokens.refresh_token);

// 2) 调用受保护接口
const me = await authApi.me();
const devices = await devicesApi.list();

// 3) 设备注册
const device = await devicesApi.register({
  name: "仓库办公室-PC",
  fingerprint: fingerprintHex,
  device_type: "DESKTOP",
  app_version: "0.1.0",
});

// 4) 状态流：优先 SSE，失败降级轮询
const es = new EventSource(`${eventsApi.streamUrl()}`, { withCredentials: false });
// 注：EventSource 无法自定义 Authorization 头；生产需改用 fetch + ReadableStream
//     或服务端支持 token 查询参数。连接失败时按 X-Polling-Interval 降级：
//     const snapshot = await eventsApi.snapshot();

// 5) 错误处理：只按 code 判断行为，不解析中文文案
try {
  await devicesApi.list();
} catch (e) {
  if (e instanceof ApiRequestError) {
    if (e.code === "AUTH_REQUIRED" && e.details.reason === "REFRESH_TOKEN_REUSED") {
      // 全部会话已被撤销，必须重新登录
    }
    if (e.code === "LICENSE_EXPIRED") { /* 引导续期 */ }
    if (e.details.stub === true) { /* 端点未实现，M3 交付 */ }
  }
}
```

### 6.2 Python（桌面端，`apps/workbench-desktop/app/infrastructure/api_client/http_client.py`）

桌面端实际调用的 6 个端点：

```python
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/account/me
POST /api/v1/devices/register
GET  /api/v1/devices
GET  /api/v1/events/snapshot   # 轮询降级入口
GET  /health
```

配置：`WORKBENCH_API_URL`（默认 `http://localhost:8000`）；`WORKBENCH_OFFLINE=1` 时注入离线占位客户端，跳过登录与 SSE，本地核心工作不中断。

### 6.3 cURL 完整链路

```bash
BASE=http://localhost:8000

# 1. 健康检查
curl -s "$BASE/health"

# 2. 登录并保存令牌
TOKENS=$(curl -s -X POST "$BASE/api/v1/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo_merchant","password":"<pwd>","client_type":"DESKTOP"}')
AT=$(echo "$TOKENS" | python -c "import sys,json;print(json.load(sys.stdin)['data']['tokens']['access_token'])")
RT=$(echo "$TOKENS" | python -c "import sys,json;print(json.load(sys.stdin)['data']['tokens']['refresh_token'])")

# 3. 查询账号上下文
curl -s "$BASE/api/v1/account/me" -H "Authorization: Bearer $AT"

# 4. 注册设备
curl -s -X POST "$BASE/api/v1/devices/register" \
  -H "Authorization: Bearer $AT" -H 'Content-Type: application/json' \
  -d '{"name":"WH-PC-01","fingerprint":"a1b2c3d4e5f6","device_type":"DESKTOP","app_version":"0.1.0"}'

# 5. 订阅状态流（SSE）
curl -N "$BASE/api/v1/events/stream" -H "Authorization: Bearer $AT"

# 6. 刷新令牌（注意：刷新后必须替换本地 RT）
curl -s -X POST "$BASE/api/v1/auth/refresh" \
  -H 'Content-Type: application/json' -d "{\"refresh_token\":\"$RT\"}"

# 7. 注销
curl -s -X POST "$BASE/api/v1/auth/logout" -H "Authorization: Bearer $AT"
```

---

## 七、进程内接口（分析引擎，非 HTTP）

> 分析能力目前**只以 Python 进程内 API 形式提供**，由桌面工作台直接调用，无网络边界、无鉴权层。

### 7.1 引擎接口（`warehouse_engine.engine.WarehouseEngine`）

| 成员 | 签名 | 说明 |
|---|---|---|
| `engine_version` | `str` = `"0.2.0"` | 引擎实现版本 |
| `formula_version` | `str` = `"0.1.0"` | 公式口径版本 |
| `validate_dataset` | `(request: AnalysisRequest, dataset: EngineDataset) -> ValidationReport` | 输入校验，不改变入参 |
| `analyze` | `(request, dataset, progress: Callable[[float], None] \| None = None) -> AnalysisResult` | 先校验后计算 |
| `list_capabilities` | `() -> list[CapabilityDescriptor]` | 返回 5 个能力描述符 |

**`analyze()` 契约**

1. **不修改传入的 `request` 与 `dataset`**；
2. 校验阻断时抛 `DataValidationError`（`code=DATA_VALIDATION_FAILED`），`details` = 每个 issue 的 `model_dump(mode="json")`；
3. **progress 阶段**：`0.0`（校验开始）→ `0.3`（校验完成）→ `0.9`（KPI 计算完成）→ `1.0`（结果组装完成）；
4. **同输入重复调用，序列化结果逐字节一致**（确定性，无随机性、无时间依赖）；
5. 当前只调用 `inventory_kpi` 一个计算器；abc-aging / replenishment / forecasting / benchmark 四类以 `code=ANALYSIS_PLACEHOLDER`（`severity=INFO`、`blocking=False`）的非阻断 Warning 标注。

### 7.2 `AnalysisRequest`（请求）

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `schema_version` | string | ❌ | `"1.0"` | 契约版本 |
| `run_id` | string | ✅ | — | 运行标识，原样写入结果 |
| `start_date` | date | ✅ | — | 期间起点（含） |
| `end_date` | date | ✅ | — | 期间终点（**不含**，左闭右开） |
| `warehouse_ids` | string[] | ✅ | — | 仓库范围；**空列表 = 不限仓库** |
| `filters` | object | ❌ | `{}` | 筛选条件（自由字典） |
| `parameters` | object | ❌ | `{}` | 计算参数（自由字典） |

模型级校验：`end_date >= start_date`，违反抛 Pydantic `ValueError`（**不是 `EngineError`**）。

### 7.3 `EngineDataset`（数据集）

| 分区 | 元素模型 | 必填 | 默认 |
|---|---|---|---|
| `skus` | `SkuRecord[]` | ❌ | `[]` |
| `movements` | `MovementRecord[]` | ❌ | `[]` |
| `snapshots` | `SnapshotRecord[]` | ❌ | `[]` |
| `replenishment` | `ReplenishmentRecord[]` | ❌ | `[]` |

**`MovementRecord` 关键字段**

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `event_id` | string | ✅ | 唯一，重复即阻断 |
| `sku_id` | string | ✅ | 必须在 `skus` 中 |
| `move_type` | `MoveType` | ✅ | 7 值枚举 |
| `quantity` | Decimal 字符串 | ✅ | **`> 0`**（严格为正） |
| `move_date` | date | ✅ | 期间归属依据 |
| `occurred_at` | datetime | ✅ | UTC ISO 8601 |
| `warehouse_id` | string | ✅ | — |
| `unit_cost` | Decimal 字符串 \| null | ❌ | `>= 0` |
| `lot_id` | string \| null | ❌ | **M1 不参与计算** |
| `source` | `EventSource` | ✅ | 4 值枚举 |
| `reversal_of` | string \| null | ❌ | 仅 `REVERSAL` 使用 |

### 7.4 `AnalysisResult`（结果）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | ❌ | `"1.0"` |
| `run_id` | string | ✅ | 回写请求 |
| `engine_version` | string | ✅ | 如 `0.2.0` |
| `formula_version` | string | ✅ | 如 `0.1.0` |
| `period_start` / `period_end` | date | ✅ | 回写请求期间 |
| `metrics` | `ResultMetric[]` | ❌ | 当前固定 9 条 |
| `warnings` | `Warning[]` | ❌ | 校验层 + 计算层 + 占位 |
| `summary` | string | ✅ | 引擎硬编码文案 |
| `input_summary` | `InputSummary` | ✅ | 含 `dataset_digest`（SHA-256） |
| `data_quality` | `DataQualityEntry[]` \| null | ❌ | 按 Warning 码汇总 |

**`ResultMetric`**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | ✅ | 如 `KPI.TURNOVER` |
| `value` | number \| integer \| string | ✅ | **不支持 null**；null 以字符串 `"null"` 表示 |
| `unit` | string | ✅ | `件` / `ratio` / `CNY` / `次/期间` / `天` |
| `formula_id` | string | ✅ | `F-KPI-001`…`F-COGS-001` |
| `formula_version` | string | ✅ | 固定 `"0.1.0"` |
| `sample_count` | integer | ✅ | 参与统计的 SKU 数（F-KPI-004 例外，为其自身分母） |
| `reason` | string \| null | ❌ | null/降级原因 |

**`reason` 完整取值（仅此 6 个）**

| reason | 含义 | 出现于 |
|---|---|---|
| `empty_dataset` | 动销率分母为 0 | F-KPI-004 |
| `avg_inventory_value_nonpositive` | 平均库存价值 ≤ 0 | F-KPI-006 / 007 |
| `turnover_nonpositive` | 周转率 ≤ 0 | F-KPI-006 / 007 |
| `nonpositive_closing` | 期末 ≤ 0 | F-KPI-008 |
| `zero_period_days` | 期间天数 = 0 | F-KPI-008 |
| `no_outflow` | 期间无出库 | F-KPI-008 |

**`Warning`**：`code`(✅) / `severity`(`INFO`/`WARN`/`ERROR`，✅) / `message`(✅) / `fields`(string[]) / `blocking`(bool，当前全部为 `false`)

**实际会产生的 Warning 码（全量 5 个）**

| Warning 码 | severity | 产生位置 | 含义 |
|---|---|---|---|
| `ANALYSIS_PLACEHOLDER` | `INFO` | 引擎 | 四类计算器未实现 |
| `NEGATIVE_BALANCE` | `WARN` | 重放层 | 期内某时点余额为负 |
| `PERIOD_MISMATCH` | `WARN` | 校验层 | `move_date` 超出 `[start_date, end_date)` |
| `NEGATIVE_BALANCE` | `WARN` | 校验层 | 快照 `quantity` 为负 |
| `UNIT_COST_MISSING` | `WARN` | KPI 计算器 | SKU 缺失单位成本 |

**`InputSummary`**：`sku_count` / `movement_count` / `snapshot_count` / `period_start` / `period_end` / `dataset_digest`（全部必填）

`dataset_digest` 算法（保证可复现）：

```python
canonical_json = dataset.model_dump_json()      # 字段顺序固定，Decimal 序列化为字符串
digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
```

### 7.5 `ValidationReport`

| 字段 | 类型 | 说明 |
|---|---|---|
| `valid` | bool | `valid = not issues`（**warnings 不影响 valid**） |
| `issues` | `ValidationIssue[]` | 阻断问题 |
| `warnings` | `Warning[]` | 非阻断警告 |

`ValidationIssue`：`row`(int\|null，分区内下标从 0 开始) / `field`(如 `movements.quantity`) / `reason`(中文) / `code`(`ErrorCode`)

### 7.6 引擎校验规则

**阻断规则（产生 issue）**

| 规则 | 错误码 | 判定 |
|---|---|---|
| 精度 | `DATA_VALIDATION_FAILED` | 数量类小数位 > **3**；金额类小数位 > **2** |
| 重复事件 | `DUPLICATE_EVENT` | 同一 `event_id` 出现多次（对后续每个重复项各发一条） |
| SKU 引用 | `SKU_NOT_FOUND` | 流水引用了 `dataset.skus` 中不存在的 SKU |
| 冲销引用缺失 | `DATA_VALIDATION_FAILED` | `REVERSAL` 缺少 `reversal_of`，或其指向的 `event_id` 不存在 |
| 冲销引用成环 | `DATA_VALIDATION_FAILED` | 冲销引用链成环（含自引用） |
| 盘点实盘数量 | `DATA_VALIDATION_FAILED` | `STOCKTAKE` 的 `quantity` ≤ 0 |

**警告规则（产生 warning，不影响 `valid`）**

| 规则 | Warning 码 | 判定 |
|---|---|---|
| 负库存快照 | `NEGATIVE_BALANCE` | `snapshots.quantity < 0` |
| 期间不匹配 | `PERIOD_MISMATCH` | `move_date < start_date` 或 `move_date >= end_date`（**左闭右开**） |

**精度规则覆盖字段**

| 类别 | 上限 | 字段 |
|---|---|---|
| 数量（scale ≤ 3） | 3 | `movements.quantity`、`snapshots.quantity`、`replenishment.avg_daily_demand`、`replenishment.lead_time_days` |
| 金额（scale ≤ 2） | 2 | `skus.unit_cost`、`movements.unit_cost`、`snapshots.inventory_value`、`replenishment.order_cost`、`replenishment.holding_cost` |

### 7.7 引擎异常

| 异常类 | 默认 code | 触发 |
|---|---|---|
| `EngineError` | `INTERNAL_ERROR` | 基类，一般不直接抛 |
| `DataValidationError` | `DATA_VALIDATION_FAILED` | `analyze()` 校验未通过；冲销引用解析失败 |
| `AnalysisCancelledError` | `ANALYSIS_CANCELLED` | **代码中从未被抛出**（`analyze()` 无取消检查点） |

> 引擎错误**无 retryable 字段**（代码中不存在），语义上 `DataValidationError` 属「必须改数据」的不可重试错误。

### 7.8 调用示例（Python）

```python
from contracts.analysis import AnalysisRequest, EngineDataset
from warehouse_engine.engine import WarehouseEngine
from warehouse_engine.errors import DataValidationError

engine = WarehouseEngine()
request = AnalysisRequest(
    run_id="run-20260831-0001",
    start_date="2026-06-01",
    end_date="2026-07-01",          # 左闭右开
    warehouse_ids=["WH-01"],        # 空列表 = 不限仓库
)
dataset = EngineDataset(skus=[...], movements=[...], snapshots=[...])

# 先校验
report = engine.validate_dataset(request, dataset)
if not report.valid:
    for issue in report.issues:
        print(f"[{issue.code.value}] {issue.field}: {issue.reason}")
    raise SystemExit(1)

# 再分析（带进度回调）
try:
    result = engine.analyze(request, dataset, progress=lambda f: print(f"{f:.0%}"))
except DataValidationError as exc:
    print("阻断：", exc.message, exc.details)
    raise

print(result.input_summary.dataset_digest)
for m in result.metrics:
    print(f"{m.name} = {m.value} {m.unit}   ({m.formula_id} @ {m.formula_version})")
```

输出指标顺序固定：`F-KPI-001 → 002 → 003 → 004 → 005 → F-COGS-001 → 006 → 007 → 008`。

---

## 八、同步信封契约（M3 预留）

`packages/contracts-schema/sync-envelope.schema.json`：`additionalProperties: false`，**10 个字段全部必填**。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `event_id` | string | `^evt_[0-9A-Za-z_-]+$` | 事件唯一标识 |
| `merchant_id` | string | `^mch_[0-9A-Za-z_-]+$` | 商户标识 |
| `target_device_id` | string | `^dev_[0-9A-Za-z_-]+$` | 目标设备标识 |
| `idempotency_key` | string | `^[0-9A-Za-z_-]{8,128}$` | 幂等键（云端去重） |
| `algorithm` | string | 常量 `AES-256-GCM` | 加密算法 |
| `nonce` | string | `^[A-Za-z0-9_-]+$` | base64url，不允空 |
| `ciphertext` | string | `^[A-Za-z0-9_-]+$` | 加密业务载荷，base64url，不允空 |
| `created_at` | string(date-time) | — | 信封创建时间（UTC） |
| `expires_at` | string(date-time) | 必须晚于 `created_at` | 信封过期时间（**应用层校验**，draft 2020-12 无法表达跨字段比较） |

**安全边界**：云端只校验商户、目标设备、大小、时间和幂等键，**不解密业务内容**；工作台解密后本地校验 `sku_id`、数量、仓库、库位和批次。
TTL 30 分钟，工作台 ACK 后删除密文（DECISIONS.md D-004）。

---

## 九、版本与兼容性

| 规则 | 说明 |
|---|---|
| 版本位置 | URL 前缀 `/api/v1`；OpenAPI `info.version = 0.1.0` |
| 唯一来源 | `packages/contracts-schema/openapi.json` 由 control-plane 导出（DECISIONS.md D-009） |
| Web 类型生成 | `pnpm --filter web generate:api`（`openapi-typescript` → `src/api/types/schema.d.ts`） |
| CI 防漂移 | `tests/test_openapi_sync.py` 断言重导出结果与磁盘快照逐字节一致；`tests/contract/test_schema_sync.py` 断言 JSON Schema 与 Pydantic 模型一致 |
| 重新导出 | `uv run python scripts/export_openapi.py`、`uv run python scripts/export_schemas.py` |
| 破坏性变更 | 删除公共字段、改类型或改单位必须提升 `schema_version`，补正反例与迁移说明，并经 A、B 共同评审 |
| 兼容矩阵 | 见 `docs/compatibility-matrix.md` |

---

## 十、已知限制

| # | 限制 | 影响 | 建议 |
|---|---|---|---|
| A-1 | 分析能力无 HTTP 接口 | Web 端无法直接发起分析 | 若需远程分析，需新增网关端点 |
| A-2 | `EventSource` 无法自定义 `Authorization` 头 | Web 端 SSE 鉴权受限 | 生产需改用 `fetch + ReadableStream` 或服务端支持 token 查询参数 |
| A-3 | Web 端令牌存于 localStorage | XSS 风险 | 生产前改 httpOnly Cookie 或内存 + 轮换 |
| A-4 | stub 端点复用 `INTERNAL_ERROR` 返回 501 | 客户端需靠 `details.stub` 区分「未实现」与「服务端错误」 | 建议新增 `NOT_IMPLEMENTED` 错误码 |
| A-5 | 12 个错误码未列入 HTTP 状态映射 | 被抛出时一律落到 400 | 建议补全 `ERROR_STATUS` 映射 |
| A-6 | `GET /api/v1/devices` 无分页 | 设备量大时一次性返回 | 后续补分页参数 |
| A-7 | 登录/刷新路由为 `async def` 但内部调用同步服务（含 Argon2 哈希与仓储 IO） | 阻塞事件循环 | 建议改为 `def`（线程池）或改用异步驱动 |
| A-8 | `analysis-request.schema.json` 不含 `EngineDataset` | 数据集段无落盘 Schema | 建议补充 `engine-dataset.schema.json` |
| A-9 | 两个分析 Schema 无 `$id` / `$schema` | 无法做 `$ref` 引用与版本化 URI | 建议补充 |
| A-10 | `list_feature_grants` 两种实现均只返回 `enabled=true` | 协议文档串未声明该过滤语义 | 建议在端口接口文档串中明确 |
