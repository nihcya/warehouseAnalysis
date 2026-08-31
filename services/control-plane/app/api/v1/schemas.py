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
