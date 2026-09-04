"""鉴权依赖（M2 实装）：真实 JWT 校验 + Scope + 租户隔离 + 许可证放行。

- ``get_container``：从 ``app.state.container`` 取组合根（测试可注入）；
- ``require_principal``：解析 Bearer 令牌，无效/缺失 → 401 ``AUTH_REQUIRED``；
- ``require_tenant_access``：商户接口依赖，Scope 含 ``merchant``，
  数据范围由令牌的 ``tenant_id`` 决定，**不信任前端传入的 merchant_id**（主基线 §5）；
- ``require_developer_scope``：开发者接口依赖，Scope 含 ``developer``，
  商户 Scope 访问开发者接口 → 403 ``AUTH_FORBIDDEN``；
- ``require_tenant_with_license``：在租户依赖之上再做许可证放行判定
  （超出离线宽限期 → 403 ``LICENSE_EXPIRED``）；``/account/me`` 与登录不挂该依赖，
  保证用户能看到"已过期"状态而不是被登录流程挡在外面。

M0 的 dev token（``Bearer merchant``）已下线：客户端必须改用 ``/auth/login`` 换发令牌。
"""

from __future__ import annotations

from contracts import ErrorCode
from fastapi import Request

from app.api.v1.errors import ApiError
from app.container import Container
from app.infrastructure.auth.scopes import principal_from_authorization
from app.infrastructure.auth.tokens import Principal

#: 商户（租户）接口所需 scope
TENANT_SCOPE = "merchant"
#: 开发者接口所需 scope
DEVELOPER_SCOPE = "developer"


def get_container(request: Request) -> Container:
    """取组合根容器（由 create_app 挂到 app.state）。"""
    container: Container = request.app.state.container
    return container


def get_auth_service(request: Request):
    """取认证应用服务。"""
    return get_container(request).auth


def get_device_service(request: Request):
    """取设备应用服务。"""
    return get_container(request).device_service


def get_entitlement_service(request: Request):
    """取许可证应用服务。"""
    return get_container(request).entitlement_service


def get_config_service(request: Request):
    """取配置版本应用服务（M3）。"""
    return get_container(request).config_service


def get_task_service(request: Request):
    """取调度任务应用服务（M3）。"""
    return get_container(request).task_service


def get_heartbeat_service(request: Request):
    """取设备心跳应用服务（M3）。"""
    return get_container(request).heartbeat_service


def get_sync_service(request: Request):
    """取同步信封应用服务（M3）。"""
    return get_container(request).sync_service


def get_hub(request: Request):
    """取状态流事件中心。"""
    return get_container(request).hub


def get_request_id(request: Request) -> str:
    """取请求 request_id（由 RequestIDMiddleware 写入），缺失时返回空串。"""
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, str) else ""


def optional_principal(request: Request) -> Principal | None:
    """解析令牌；无凭证或无效返回 None（不抛错，供可匿名端点使用）。"""
    secret = get_container(request).settings.resolve_auth_secret()
    return principal_from_authorization(request.headers.get("Authorization"), secret)


def require_valid_token(request: Request) -> Principal:
    """要求**令牌本身**有效（签名与过期），不校验会话是否已被撤销。

    仅用于注销：会话已撤销时仍要允许"再注销一次"并返回幂等成功，
    否则用户在令牌未过期期间重复点击注销会收到 401。
    """
    principal = optional_principal(request)
    if principal is None:
        raise ApiError(
            code=ErrorCode.AUTH_REQUIRED,
            message="缺少或无效的凭证。",
            status_code=401,
        )
    return principal


def require_principal(request: Request) -> Principal:
    """要求有效令牌且会话未被撤销：缺失、过期、被篡改、已注销一律 401。

    Access Token 是无状态 JWT，但会话可撤销（注销、刷新重放检测），
    故每次受保护请求都回查一次会话状态，保证"注销立即生效"而不是等到令牌过期。
    """
    principal = require_valid_token(request)
    session = get_container(request).identity.get_session(principal.session_id)
    if session is None or session.revoked_at is not None:
        raise ApiError(
            code=ErrorCode.AUTH_REQUIRED,
            message="会话已撤销，请重新登录。",
            status_code=401,
        )
    return principal


def _require_scope(request: Request, scope: str) -> Principal:
    """校验令牌是否含目标 Scope：无凭证 401，Scope 不足 403。"""
    principal = require_principal(request)
    if not principal.has_scope(scope):
        raise ApiError(
            code=ErrorCode.AUTH_FORBIDDEN,
            message=f"缺少 {scope} Scope，无权访问该接口。",
            details={"required_scope": scope},
            status_code=403,
        )
    return principal


async def require_tenant_access(request: Request) -> Principal:
    """商户侧接口依赖：Scope 含 ``merchant``。"""
    return _require_scope(request, TENANT_SCOPE)


async def require_developer_scope(request: Request) -> Principal:
    """开发者侧接口依赖：Scope 含 ``developer``。"""
    return _require_scope(request, DEVELOPER_SCOPE)


async def require_tenant_with_license(request: Request) -> Principal:
    """商户侧接口依赖 + 许可证放行判定（超出离线宽限期 → 403 ``LICENSE_EXPIRED``）。"""
    principal = _require_scope(request, TENANT_SCOPE)
    # 许可证判定抛 ControlPlaneError，由全局处理器统一渲染（不在此处重复映射）
    get_entitlement_service(request).require_allowed(principal.tenant_id)
    return principal
