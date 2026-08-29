"""api/v1 stub 路由（M0 骨架）。

- 受保护端点统一挂 Scope 依赖（``require_tenant_access`` / ``require_developer_scope``）；
- 业务逻辑未实现：统一抛 501（code=INTERNAL_ERROR，details 标注 ``stub`` 与端点）；
- 路由层不直接写 ORM：事务经 application 用例完成（M1+ 落地）。
"""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, Depends

from app.api.v1.deps import require_developer_scope, require_tenant_access
from app.api.v1.errors import not_implemented
from app.api.v1.schemas import ErrorResponse, LoginRequest, RefreshRequest

router = APIRouter()


def _stub_responses(*, scoped: bool = False) -> dict[int | str, dict[str, Any]]:
    """生成 stub 路由的 OpenAPI 响应声明：统一 501；受保护端点另加 401/403。"""
    responses: dict[int | str, dict[str, Any]] = {
        501: {"model": ErrorResponse, "description": "M0 未实现（stub）"},
    }
    if scoped:
        responses[401] = {"model": ErrorResponse, "description": "缺少 Authorization 凭证"}
        responses[403] = {"model": ErrorResponse, "description": "缺少所需 Scope"}
    return responses


def _raise_stub(endpoint: str) -> NoReturn:
    """抛出统一 501 未实现错误。"""
    raise not_implemented(endpoint)


@router.post(
    "/auth/login",
    tags=["auth"],
    responses=_stub_responses(),
    summary="账号登录（stub）",
)
async def login(body: LoginRequest) -> None:
    """账号登录（M0 stub：恒 501；真实认证在 M2 交付）。"""
    _raise_stub("POST /api/v1/auth/login")


@router.post(
    "/auth/refresh",
    tags=["auth"],
    responses=_stub_responses(),
    summary="刷新访问令牌（stub）",
)
async def refresh(body: RefreshRequest) -> None:
    """刷新访问令牌（M0 stub：恒 501）。"""
    _raise_stub("POST /api/v1/auth/refresh")


@router.post(
    "/auth/logout",
    tags=["auth"],
    responses=_stub_responses(),
    summary="退出登录（stub）",
)
async def logout() -> None:
    """退出登录（M0 stub：恒 501）。"""
    _raise_stub("POST /api/v1/auth/logout")


@router.post(
    "/devices/register",
    tags=["devices"],
    responses=_stub_responses(),
    summary="设备注册（stub）",
)
async def register_device() -> None:
    """设备注册（M0 stub：首次激活流程，不挂 Scope 依赖，恒 501）。"""
    _raise_stub("POST /api/v1/devices/register")


@router.get(
    "/devices",
    tags=["devices"],
    dependencies=[Depends(require_tenant_access)],
    responses=_stub_responses(scoped=True),
    summary="设备列表（stub）",
)
async def list_devices() -> None:
    """查询当前商户的设备列表（M0 stub：恒 501）。"""
    _raise_stub("GET /api/v1/devices")


@router.get(
    "/config",
    tags=["config"],
    dependencies=[Depends(require_tenant_access)],
    responses=_stub_responses(scoped=True),
    summary="拉取配置（stub）",
)
async def get_config() -> None:
    """拉取商户生效配置（M0 stub：恒 501）。"""
    _raise_stub("GET /api/v1/config")


@router.get(
    "/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_tenant_access)],
    responses=_stub_responses(scoped=True),
    summary="任务列表（stub）",
)
async def list_tasks() -> None:
    """查询商户调度任务（M0 stub：恒 501）。"""
    _raise_stub("GET /api/v1/tasks")


@router.post(
    "/tasks/pull",
    tags=["tasks"],
    dependencies=[Depends(require_tenant_access)],
    responses=_stub_responses(scoped=True),
    summary="拉取待执行任务（stub）",
)
async def pull_tasks() -> None:
    """设备拉取待执行任务（M0 stub：恒 501）。"""
    _raise_stub("POST /api/v1/tasks/pull")


@router.get(
    "/sync/events/pull",
    tags=["sync"],
    dependencies=[Depends(require_tenant_access)],
    responses=_stub_responses(scoped=True),
    summary="拉取同步事件（stub）",
)
async def pull_sync_events() -> None:
    """拉取待同步的加密事件信封（M0 stub：恒 501）。"""
    _raise_stub("GET /api/v1/sync/events/pull")


@router.post(
    "/sync/ack",
    tags=["sync"],
    dependencies=[Depends(require_tenant_access)],
    responses=_stub_responses(scoped=True),
    summary="确认同步事件（stub）",
)
async def ack_sync() -> None:
    """确认同步事件已应用（M0 stub：恒 501）。"""
    _raise_stub("POST /api/v1/sync/ack")


@router.get(
    "/telemetry",
    tags=["telemetry"],
    dependencies=[Depends(require_developer_scope)],
    responses=_stub_responses(scoped=True),
    summary="技术日志查询（开发者，stub）",
)
async def get_telemetry() -> None:
    """开发者按商户 / 设备 / 版本 / 错误码筛选技术日志（M0 stub：恒 501）。"""
    _raise_stub("GET /api/v1/telemetry")


@router.get(
    "/merchants",
    tags=["merchants"],
    dependencies=[Depends(require_developer_scope)],
    responses=_stub_responses(scoped=True),
    summary="商户列表（开发者，stub）",
)
async def list_merchants() -> None:
    """开发者查询商户列表（M0 stub：恒 501）。"""
    _raise_stub("GET /api/v1/merchants")


@router.post(
    "/heartbeat",
    tags=["heartbeat"],
    dependencies=[Depends(require_tenant_access)],
    responses=_stub_responses(scoped=True),
    summary="设备心跳（stub）",
)
async def heartbeat() -> None:
    """设备心跳上报（M0 stub：恒 501）。"""
    _raise_stub("POST /api/v1/heartbeat")
