"""结构化结果构造：输入摘要（dataset_digest）与 AnalysisResult 组装辅助。"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from warehouse_engine.contracts import (
    AnalysisRequest,
    AnalysisResult,
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


def build_analysis_result(
    request: AnalysisRequest,
    dataset: EngineDataset,
    *,
    engine_version: str,
    formula_version: str,
    summary: str,
    metrics: Sequence[ResultMetric] = (),
    warnings: Sequence[Warning] = (),
) -> AnalysisResult:
    """按契约组装 AnalysisResult（run_id、版本、期间与输入摘要齐全）。"""
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
    )
