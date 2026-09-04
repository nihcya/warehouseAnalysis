"""api/v1 请求与响应模型（M2）。

响应统一为 ``{"data": ...}``（成功）或 ``{"error": {...}}``（失败），
与 ``app/api/v1/errors.py`` 的渲染口径一致。

- 成功信封按端点逐个显式声明（不使用泛型），保证 OpenAPI 中的 Schema 名可读；
- 日期时间统一 ISO 8601 UTC（主基线 §35.5：云端存 UTC，Web 端按时区转换展示）；
- 响应模型只暴露展示所需字段：不含密码哈希、令牌指纹等内部字段。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    """统一错误体：``{"error": ...}`` 的内层字段。"""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str


class ErrorResponse(BaseModel):
    """统一错误响应。"""

    error: ErrorBody


class HealthResponse(BaseModel):
    """/health 响应：应用状态、版本与控制库可达性。"""

    status: str
    app_version: str
    database: str


class LoginRequest(BaseModel):
    """登录请求。

    ``client_type`` 与 ``device_id`` 可选：桌面工作台注册设备后登录时带上，
    便于把会话与设备关联（Web 登录默认 WEB）。
    """

    username: str
    password: str
    client_type: str = "WEB"
    device_id: str | None = None


class RefreshRequest(BaseModel):
    """刷新访问令牌请求。"""

    refresh_token: str


class DeviceRegisterRequest(BaseModel):
    """设备注册请求：``(tenant_id, fingerprint)`` 唯一，重复注册按幂等处理。"""

    name: str
    fingerprint: str
    device_type: str = "DESKTOP"
    app_version: str | None = None


class HeartbeatRequest(BaseModel):
    """设备心跳请求（M3）。

    ``device_id`` 必须归属当前令牌的租户（服务端校验，不信任其它租户字段）；
    ``status`` 为工作台 Agent 自报运行状态文本（如 ``RUNNING`` / ``IDLE``）。
    """

    device_id: str
    status: str
    app_version: str | None = None
    engine_version: str | None = None
    db_schema_version: str | None = None
    pending_sync_count: int = Field(default=0, ge=0)


class TaskPullRequest(BaseModel):
    """设备拉取待执行任务请求（M3）。"""

    device_id: str
    limit: int = Field(default=10, ge=1, le=50)


class SyncAckRequest(BaseModel):
    """同步信封确认请求（M3）：单个信封，应用成功后逐一确认。"""

    envelope_id: str


class SyncInjectRequest(BaseModel):
    """Mock 小程序事件注入请求（M3 dev 工具，生产环境禁用）。

    ``payload`` 为模拟的小程序事件明文，服务端加密为密文信封后落库；
    ``event_id`` 缺省由服务端生成，显式传入便于幂等重放联调。
    """

    target_device_id: str
    payload: dict[str, Any]
    event_id: str | None = None
    idempotency_key: str | None = None
    ttl_seconds: int = Field(default=7 * 24 * 60 * 60, ge=60, le=30 * 24 * 60 * 60)


# ---- 成功响应载荷 ----


class TokenData(BaseModel):
    """令牌对。"""

    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    refresh_expires_at: datetime


class AccountData(BaseModel):
    """账号信息（不含凭证字段）。"""

    account_id: str
    login_name: str
    role: str
    tenant_id: str | None = None
    status: str
    last_login_at: datetime | None = None


class TenantData(BaseModel):
    """商户信息。"""

    tenant_id: str
    name: str
    status: str
    product_profile_id: str | None = None


class LicenseData(BaseModel):
    """许可证与离线宽限期评估结果。

    ``status`` 取值：``ACTIVE`` / ``GRACE`` / ``EXPIRED`` / ``REVOKED`` / ``MISSING``；
    ``GRACE`` 表示已过期但仍在离线宽限期内，此时接口仍放行，UI 必须明示剩余天数。
    """

    status: str
    license_id: str | None = None
    product_profile_code: str | None = None
    starts_at: date | None = None
    expires_at: date | None = None
    grace_days: int = 0
    grace_ends_at: date | None = None
    days_remaining: int | None = None
    max_devices: int = 0
    features: list[str] = Field(default_factory=list)


class AuthData(BaseModel):
    """登录 / 刷新响应载荷。"""

    tokens: TokenData
    account: AccountData
    tenant: TenantData | None = None
    license: LicenseData


class LoginResponse(BaseModel):
    """登录响应。"""

    data: AuthData


class RefreshResponse(BaseModel):
    """刷新响应（与登录同构，Refresh Token 已轮换）。"""

    data: AuthData


class LogoutData(BaseModel):
    """注销响应载荷（重复注销幂等成功）。"""

    session_id: str
    revoked: bool = True


class LogoutResponse(BaseModel):
    """注销响应。"""

    data: LogoutData


class AccountMeData(BaseModel):
    """当前账号上下文。"""

    account: AccountData
    tenant: TenantData | None = None
    license: LicenseData


class AccountMeResponse(BaseModel):
    """``GET /account/me`` 响应。"""

    data: AccountMeData


class DeviceData(BaseModel):
    """设备信息。"""

    device_id: str
    tenant_id: str
    name: str
    device_type: str
    fingerprint: str
    status: str
    app_version: str | None = None
    last_seen_at: datetime | None = None
    registered_at: datetime | None = None


class DeviceRegisterResponse(BaseModel):
    """设备注册响应。"""

    data: DeviceData


class DeviceListResponse(BaseModel):
    """设备列表响应。"""

    data: list[DeviceData]


class SnapshotData(BaseModel):
    """状态流快照（SSE 首帧与轮询降级共用同一结构）。"""

    event_id: int
    generated_at: datetime
    license: LicenseData
    devices: list[DeviceData]
    pending_sync_count: int | None = None
    tasks: list[dict[str, Any]] = Field(default_factory=list)


class SnapshotResponse(BaseModel):
    """``GET /events/snapshot`` 响应（轮询降级入口）。"""

    data: SnapshotData


# ---- M3：心跳 / 配置 / 任务 / 同步 ----


class HeartbeatData(BaseModel):
    """设备心跳最新投影。"""

    device_id: str
    tenant_id: str
    sent_at: datetime
    status: str
    app_version: str | None = None
    engine_version: str | None = None
    db_schema_version: str | None = None
    pending_sync_count: int


class HeartbeatResponse(BaseModel):
    """``POST /heartbeat`` 响应。"""

    data: HeartbeatData


class ConfigData(BaseModel):
    """商户生效配置（客户端先验摘要与签名再应用，spec：配置下发与验签）。"""

    tenant_id: str
    version: str
    content: dict[str, Any]
    sha256: str
    signature: str
    schema_version: int
    status: str
    effective_at: datetime | None = None
    created_at: datetime | None = None


class ConfigResponse(BaseModel):
    """``GET /config`` 响应：无已发布配置时 ``data`` 为 null（客户端走本地缓存）。"""

    data: ConfigData | None


class TaskData(BaseModel):
    """调度任务定义（完整结果留在本地，云端只有定义与状态投影）。"""

    task_id: str
    tenant_id: str
    task_type: str
    cron_expr: str | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    enabled: bool
    created_at: datetime | None = None


class TaskListResponse(BaseModel):
    """``GET /tasks`` 响应（含禁用任务，UI 明示启用状态）。"""

    data: list[TaskData]


class TaskRunData(BaseModel):
    """任务运行投影：状态/时间/错误码，不含业务明细。"""

    run_id: str
    task_id: str
    tenant_id: str
    status: str
    device_id: str | None = None
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None


class PulledTaskData(BaseModel):
    """设备拉取到的待执行任务：任务定义 + 已锁定的运行投影。"""

    task: TaskData
    run: TaskRunData


class TaskPullResponse(BaseModel):
    """``POST /tasks/pull`` 响应。"""

    data: list[PulledTaskData]


class SyncEnvelopeData(BaseModel):
    """同步信封（密文中继）：云端只见密文，解密与校验在工作台完成。"""

    envelope_id: str
    event_id: str
    target_device_id: str
    ciphertext: str
    idempotency_key: str | None = None
    status: str
    expires_at: datetime | None = None
    created_at: datetime | None = None


class SyncEventsPullResponse(BaseModel):
    """``GET /sync/events/pull`` 响应。"""

    data: list[SyncEnvelopeData]


class SyncAckData(BaseModel):
    """同步确认结果：``already_acked`` 为 True 表示重复确认幂等成功。"""

    envelope_id: str
    already_acked: bool


class SyncAckResponse(BaseModel):
    """``POST /sync/ack`` 响应。"""

    data: SyncAckData


class SyncInjectResponse(BaseModel):
    """``POST /dev/sync/inject`` 响应（dev 工具）。"""

    data: SyncEnvelopeData
