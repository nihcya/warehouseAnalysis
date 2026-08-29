"""引擎异常体系：稳定错误码 + 结构化详情。

调用方依据 code 判断行为，不解析中文文案；details 定位到字段与原因。
"""

from __future__ import annotations

from typing import Any, ClassVar

from warehouse_engine.contracts import ErrorCode


class EngineError(Exception):
    """引擎统一异常基类：code 为稳定错误码，details 为结构化定位信息。"""

    # 未显式指定 code 时使用的默认错误码
    default_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code if code is not None else type(self).default_code
        self.details: list[dict[str, Any]] = list(details) if details is not None else []


class DataValidationError(EngineError):
    """输入数据未通过校验（阻断分析）。"""

    default_code: ClassVar[ErrorCode] = ErrorCode.DATA_VALIDATION_FAILED


class AnalysisCancelledError(EngineError):
    """分析任务被取消（进度回调或外部取消请求）。"""

    default_code: ClassVar[ErrorCode] = ErrorCode.ANALYSIS_CANCELLED
