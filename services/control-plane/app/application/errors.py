"""应用层错误（M2）。

应用层用例只抛本模块定义的错误，错误码一律取 ``contracts.enums.ErrorCode``
已冻结取值（主基线 §21.4：最小错误码集合，不私造错误码）。
api 层（``app/api/v1/errors.py``）负责把它翻译成统一 ``{"error": {...}}`` 响应。
"""

from __future__ import annotations

from typing import Any

from contracts import ErrorCode


class ControlPlaneError(Exception):
    """应用层业务异常：携带错误码、中文文案与结构化 details。"""

    def __init__(
        self,
        *,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details if details is not None else {}


def auth_required(message: str = "缺少或无效的凭证。", **details: Any) -> ControlPlaneError:
    """401：未登录、令牌无效或已撤销。"""
    return ControlPlaneError(code=ErrorCode.AUTH_REQUIRED, message=message, details=details)


def auth_forbidden(message: str, **details: Any) -> ControlPlaneError:
    """403：已认证但权限不足（Scope、账号状态、设备上限等）。"""
    return ControlPlaneError(code=ErrorCode.AUTH_FORBIDDEN, message=message, details=details)


def license_expired(message: str = "许可证已过期或不可用。", **details: Any) -> ControlPlaneError:
    """403：许可证过期（超出离线宽限期）、被吊销或缺失。"""
    return ControlPlaneError(code=ErrorCode.LICENSE_EXPIRED, message=message, details=details)


def device_revoked(message: str = "设备已被吊销，重新绑定前不可使用。", **details: Any) -> ControlPlaneError:
    """403：设备已吊销。"""
    return ControlPlaneError(code=ErrorCode.DEVICE_REVOKED, message=message, details=details)


def validation_failed(message: str = "请求数据校验失败。", **details: Any) -> ControlPlaneError:
    """400：请求数据校验失败（设备不存在、信封不存在、租户不匹配等）。"""
    return ControlPlaneError(code=ErrorCode.DATA_VALIDATION_FAILED, message=message, details=details)


def duplicate_event(message: str = "重复的事件。", **details: Any) -> ControlPlaneError:
    """409：重复事件（event_id 唯一约束拦截）。"""
    return ControlPlaneError(code=ErrorCode.DUPLICATE_EVENT, message=message, details=details)
