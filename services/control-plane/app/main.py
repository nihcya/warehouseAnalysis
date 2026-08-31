"""应用工厂与 ASGI 入口。

``create_app()`` 负责装配：CORS、request_id 中间件、统一异常处理器与路由挂载；
模块级 ``app`` 供 ``uvicorn app.main:app`` 直接运行（cwd 为 services/control-plane）。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.health import router as health_router
from app.api.v1.errors import register_exception_handlers
from app.api.v1.routes import router as api_v1_router
from app.container import Container, build_container
from app.settings import get_settings


class RequestIDMiddleware:
    """为每个 HTTP 请求生成 request_id。

    - 写入 ``scope["state"]``，供异常处理器与审计读取（``request.state.request_id``）；
    - 回写 ``X-Request-ID`` 响应头，便于云端 / 工作台跨端排查。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append("X-Request-ID", request_id)
            await send(message)

        await self.app(scope, receive, send_with_request_id)


def create_app(container: Container | None = None) -> FastAPI:
    """构建 FastAPI 应用：容器 → CORS → request_id → 异常处理器 → 路由挂载。

    ``container`` 供测试注入（如内存仓储）；缺省按配置在组合根装配
    （``app.container.build_container``）。
    """
    settings = get_settings()
    application = FastAPI(
        title="warehouse-control-plane",
        version=settings.APP_VERSION,
        description=(
            "云端控制平面 /api/v1：认证、账号、设备与状态流（M2）；"
            "心跳、任务、同步、配置与技术日志维持 stub（M3）"
        ),
    )
    # 组合根：仓储、事件中心与应用服务的进程级单例（deps 从 app.state 取用）
    application.state.container = (
        container if container is not None else build_container(settings)
    )
    # 中间件后添加者在最外层：CORS 放最外，保证错误响应同样携带 CORS 头
    application.add_middleware(RequestIDMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(api_v1_router, prefix="/api/v1")
    return application


def export_openapi_json(application: FastAPI | None = None) -> bytes:
    """渲染 OpenAPI 文档字节串；导出脚本与一致性测试共用同一序列化口径。"""
    if application is None:
        application = create_app()
    payload: dict[str, Any] = application.openapi()
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return rendered.encode("utf-8")


#: 模块级应用实例：``uvicorn app.main:app``（cwd=services/control-plane）
app = create_app()
