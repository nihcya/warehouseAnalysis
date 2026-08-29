"""Scope 鉴权依赖（M0 占位）。

dev token 协议（详见 app/infrastructure/auth/scopes.py）：

- 无 Authorization 凭证 → 401 ``AUTH_REQUIRED``；
- ``require_tenant_access``（商户依赖）：scopes 含 ``merchant`` 即通过；
- ``require_developer_scope``（开发者依赖）：scopes 含 ``developer`` 才通过，
  否则 403 ``AUTH_FORBIDDEN``（商户 Scope 无法访问 developer:* 接口）。

M2 将替换为真实 JWT 校验（签名 / 过期 / token rotation），
本模块对路由暴露的依赖签名保持不变。
"""

from __future__ import annotations

from contracts import ErrorCode
from fastapi import Request

from app.api.v1.errors import ApiError
from app.infrastructure.auth.scopes import parse_bearer_scopes

#: 商户（租户）接口所需 scope
TENANT_SCOPE = "merchant"
#: 开发者接口所需 scope
DEVELOPER_SCOPE = "developer"


def _require_scope(request: Request, scope: str) -> None:
    """校验请求携带的 scopes 是否包含目标 scope；不满足时抛统一错误。"""
    scopes = parse_bearer_scopes(request.headers.get("Authorization"))
    if scopes is None:
        raise ApiError(
            code=ErrorCode.AUTH_REQUIRED,
            message="缺少 Authorization 凭证。",
            status_code=401,
        )
    if scope not in scopes:
        raise ApiError(
            code=ErrorCode.AUTH_FORBIDDEN,
            message=f"缺少 {scope} Scope，无权访问该接口。",
            details={"required_scope": scope},
            status_code=403,
        )


async def require_tenant_access(request: Request) -> None:
    """商户侧接口依赖：M0 校验 Bearer dev token 含 merchant scope。"""
    _require_scope(request, TENANT_SCOPE)


async def require_developer_scope(request: Request) -> None:
    """开发者侧接口依赖：M0 校验 Bearer dev token 含 developer scope。"""
    _require_scope(request, DEVELOPER_SCOPE)
