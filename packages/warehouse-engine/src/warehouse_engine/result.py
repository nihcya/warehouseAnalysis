"""结构化结果构造：输入摘要（dataset_digest）、数据质量报告与 AnalysisResult 组装辅助。"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from warehouse_engine.contracts import (
    AnalysisRequest,
    AnalysisResult,
    DataQualityEntry,
    EngineDataset,
    InputSummary,
    ResultMetric,
    Warning,
)

#: 契约 Schema 版本（与 AnalysisRequest/AnalysisResult 的默认值保持一致）
SCHEMA_VERSION = "1.0"


def build_input_summary(dataset: EngineDataset, request: AnalysisRequest) -> InputSummary:
    """构建输入摘要。

    dataset_digest 为数据集规范化 JSON（model_dump_json，字段顺序固定、
    Decimal 序列化为字符串）的 SHA-256，用于结果可追溯与重复运行一致性校验。
    """
    canonical_json = dataset.model_dump_json()
    digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return InputSummary(
        sku_count=len(dataset.skus),
        movement_count=len(dataset.movements),
        snapshot_count=len(dataset.snapshots),
        period_start=request.start_date,
        period_end=request.end_date,
        dataset_digest=digest,
    )


def build_data_quality(warnings: Sequence[Warning]) -> list[DataQualityEntry]:
    """数据质量报告：Warning 按码汇总（计数 + 明细）。

    - 条目按 code 字典序排列（确定性）；
    - details 保持 Warning 在入参中的原始顺序；
    - 空输入返回空列表（表示"已生成报告，无数据质量问题"）。
    """
    grouped: dict[str, list[Warning]] = {}
    for warning in warnings:
        grouped.setdefault(warning.code, []).append(warning)
    return [
        DataQualityEntry(code=code, count=len(details), details=details)
        for code, details in sorted(grouped.items())
    ]


def build_analysis_result(
    request: AnalysisRequest,
    dataset: EngineDataset,
    *,
    engine_version: str,
    formula_version: str,
    summary: str,
    metrics: Sequence[ResultMetric] = (),
    warnings: Sequence[Warning] = (),
    data_quality: Sequence[DataQualityEntry] | None = None,
) -> AnalysisResult:
    """按契约组装 AnalysisResult（run_id、版本、期间与输入摘要齐全）。

    ``data_quality`` 为 None 时结果字段保持 None（未生成，如 M0/FakeEngine 结果）。
    """
    return AnalysisResult(
        schema_version=SCHEMA_VERSION,
        run_id=request.run_id,
        engine_version=engine_version,
        formula_version=formula_version,
        period_start=request.start_date,
        period_end=request.end_date,
        metrics=list(metrics),
        warnings=list(warnings),
        summary=summary,
        input_summary=build_input_summary(dataset, request),
        data_quality=None if data_quality is None else list(data_quality),
    )
