"""输入校验入口：外部原始 JSON 载荷 → ValidationReport。

``validate_raw_dataset`` 供外部 JSON 入口使用：
- 逐记录解析，pydantic ValidationError 转为 ValidationIssue（code=DATA_VALIDATION_FAILED）；
- 记录全部解析通过后，再执行与 ``WarehouseEngine.validate_dataset`` 复用的数据集级规则
  （无请求期间信息，跳过 PERIOD_MISMATCH 规则）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

from warehouse_engine.contracts import (
    EngineDataset,
    ErrorCode,
    MovementRecord,
    ReplenishmentRecord,
    SkuRecord,
    SnapshotRecord,
    ValidationIssue,
    ValidationReport,
)
from warehouse_engine.validation.rules import apply_dataset_rules

# 数据集分区名 → 记录模型
_SECTION_MODELS: dict[str, type[BaseModel]] = {
    "skus": SkuRecord,
    "movements": MovementRecord,
    "snapshots": SnapshotRecord,
    "replenishment": ReplenishmentRecord,
}


def _to_issue(section: str, row: int, error: ErrorDetails) -> ValidationIssue:
    """把单条 pydantic 校验错误转换为 ValidationIssue。"""
    loc = ".".join(str(part) for part in error.get("loc", ()))
    field = f"{section}.{loc}" if loc else section
    return ValidationIssue(
        row=row,
        field=field,
        reason=str(error.get("msg", "记录校验失败")),
        code=ErrorCode.DATA_VALIDATION_FAILED,
    )


def validate_raw_dataset(payload: dict[str, Any]) -> ValidationReport:
    """校验外部传入的原始数据集 JSON（结构与 EngineDataset 对齐）。

    记录级解析失败时直接返回（避免在残缺数据上误报跨记录规则），
    否则继续执行数据集级共享规则。
    """
    if not isinstance(payload, dict):
        return ValidationReport(
            valid=False,
            issues=[
                ValidationIssue(
                    row=None,
                    field="payload",
                    reason="数据集载荷必须是 JSON 对象",
                    code=ErrorCode.DATA_VALIDATION_FAILED,
                )
            ],
        )

    issues: list[ValidationIssue] = []
    records: dict[str, list[Any]] = {}
    for section, model in _SECTION_MODELS.items():
        rows = payload.get(section, [])
        if not isinstance(rows, list):
            issues.append(
                ValidationIssue(
                    row=None,
                    field=section,
                    reason=f"{section} 必须是数组",
                    code=ErrorCode.DATA_VALIDATION_FAILED,
                )
            )
            records[section] = []
            continue
        parsed: list[Any] = []
        for row, raw in enumerate(rows):
            try:
                parsed.append(model.model_validate(raw))
            except ValidationError as exc:
                issues.extend(_to_issue(section, row, error) for error in exc.errors())
        records[section] = parsed

    if issues:
        return ValidationReport(valid=False, issues=issues)

    dataset = EngineDataset(
        schema_version=payload.get("schema_version", "1.0"),
        skus=records["skus"],
        movements=records["movements"],
        snapshots=records["snapshots"],
        replenishment=records["replenishment"],
    )
    rule_issues, warnings = apply_dataset_rules(dataset)
    issues.extend(rule_issues)
    return ValidationReport(valid=not issues, issues=issues, warnings=warnings)
