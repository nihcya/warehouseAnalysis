"""api/v1 请求与响应模型（M0 骨架）。

响应统一为 ``{"data": ...}``（成功）或 ``{"error": {...}}``（失败）；
M0 所有 stub 接口仅产生错误响应，成功信封随 M1+ 首个实装接口引入。
"""

from __future__ import annotations

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
    """登录请求（M0 占位：字段仅为接口契约展示）。"""

    username: str
    password: str


class RefreshRequest(BaseModel):
    """刷新访问令牌请求（M0 占位）。"""

    refresh_token: str
