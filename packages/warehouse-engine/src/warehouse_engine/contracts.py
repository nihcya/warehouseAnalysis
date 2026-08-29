"""引擎侧公共契约类型：仅从 contracts 包重导出，不复制 DTO。

引擎与工作台（A 侧）必须引用同一份契约定义，
契约变更只能发生在 contracts-python 包中，并同步提升 schema_version。
"""

from contracts.analysis import (
    AnalysisRequest,
    AnalysisResult,
    CapabilityDescriptor,
    EngineDataset,
    InputSummary,
    MovementRecord,
    ReplenishmentRecord,
    ResultMetric,
    SkuRecord,
    SnapshotRecord,
    ValidationIssue,
    ValidationReport,
    Warning,
)
from contracts.enums import ErrorCode, EventSource, MoveType, WarningSeverity

__all__ = [
    "AnalysisRequest",
    "AnalysisResult",
    "CapabilityDescriptor",
    "EngineDataset",
    "ErrorCode",
    "EventSource",
    "InputSummary",
    "MoveType",
    "MovementRecord",
    "ReplenishmentRecord",
    "ResultMetric",
    "SkuRecord",
    "SnapshotRecord",
    "ValidationIssue",
    "ValidationReport",
    "Warning",
    "WarningSeverity",
]
