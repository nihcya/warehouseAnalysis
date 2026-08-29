"""仓库品类分析决策工具：引擎与工作台共享的数据契约包。

公开模型与枚举统一从这里导出；契约变更必须提升 schema_version
并同步 packages/contracts-schema/ 下的 JSON Schema。
"""

from contracts.analysis import (
    AnalysisRequest,
    AnalysisResult,
    CapabilityDescriptor,
    DataQualityEntry,
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
    "DataQualityEntry",
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
