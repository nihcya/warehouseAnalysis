"""统一错误模型与全局异常处理器。

所有错误响应统一为 ``{"error": {"code", "message", "details", "request_id"}}``；
错误码只取 ``contracts.enums.ErrorCode`` 已有值（M0 冻结，不私造错误码）。
"""

from __future__ import annotations

import uuid
from typing import Any

from contracts import ErrorCode
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """API 层统一业务异常：由全局处理器渲染为 ``{"error": {...}}``。"""

    def __init__(
        self,
        *,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        status_code: int,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details if details is not None else {}
        self.status_code = status_code


def not_implemented(endpoint: str) -> ApiError:
    """构造 M0 stub 的 501 未实现错误。

    ErrorCode 枚举中无 NOT_IMPLEMENTED；按 M0 约定复用 INTERNAL_ERROR，
    并在 details 中以 ``{"stub": true, "endpoint": ...}`` 明确标注，
    便于客户端与测试区分“未实现”与真实内部错误。
    """
    return ApiError(
        code=ErrorCode.INTERNAL_ERROR,
        message=f"{endpoint} 尚未在 M0 实现。",
        details={"stub": True, "endpoint": endpoint},
        status_code=501,
    )


def _request_id_of(request: Request) -> str:
    """取请求的 request_id（由 RequestIDMiddleware 写入）；缺失时现生成。"""
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, str) else uuid.uuid4().hex


def _error_body(
    code: ErrorCode,
    message: str,
    details: Any,
    request_id: str,
) -> dict[str, Any]:
    """构造统一错误体（内层字段）。"""
    return {
        "code": code.value,
        "message": message,
        "details": details,
        "request_id": request_id,
    }


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器：ApiError、请求校验错误与未捕获异常统一格式。"""

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": _error_body(exc.code, exc.message, exc.details, _request_id_of(request))
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": _error_body(
                    ErrorCode.DATA_VALIDATION_FAILED,
                    "请求数据校验失败。",
                    jsonable_encoder(exc.errors()),
                    _request_id_of(request),
                )
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # 未捕获异常一律 500 INTERNAL_ERROR：不回传堆栈与异常细节，避免泄露内部实现
        return JSONResponse(
            status_code=500,
            content={
                "error": _error_body(
                    ErrorCode.INTERNAL_ERROR,
                    "服务内部错误。",
                    {},
                    _request_id_of(request),
                )
            },
        )
