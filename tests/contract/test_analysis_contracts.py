"""契约测试：合法最小载荷通过校验；反例定位到具体字段（code=DATA_VALIDATION_FAILED）。

反例覆盖：缺 run_id、quantity 类型错、move_type 枚举非法、quantity 为负/0、
unit_cost 小数位超限（scale=3）。request 侧的 pydantic 错误在本测试内转换为
field=request.<字段> 的 ValidationIssue，与 dataset 侧 validate_raw_dataset
返回的 ValidationReport 统一断言。
"""

from __future__ import annotations

from typing import Any

from contracts import (
    AnalysisRequest,
    EngineDataset,
    ErrorCode,
    ValidationIssue,
    ValidationReport,
)
from pydantic import ValidationError
from warehouse_engine.validation import validate_raw_dataset


def _minimal_request_payload() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "run_id": "run-contract-0001",
        "start_date": "2026-06-01",
        "end_date": "2026-06-30",
        "warehouse_ids": ["WH-01"],
        "filters": {},
        "parameters": {},
    }


def _minimal_dataset_payload() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "skus": [
            {
                "sku_id": "SKU-0001",
                "name": "矿泉水 550ml",
                "category": "饮料",
                "unit": "瓶",
                "unit_cost": "1.20",
                "currency": "CNY",
            }
        ],
        "movements": [
            {
                "event_id": "EVT-0001",
                "sku_id": "SKU-0001",
                "move_type": "OUTBOUND",
                "quantity": "10",
                "move_date": "2026-06-05",
                "occurred_at": "2026-06-05T10:00:00Z",
                "warehouse_id": "WH-01",
                "unit_cost": "1.20",
                "source": "IMPORT",
            }
        ],
        "snapshots": [
            {
                "sku_id": "SKU-0001",
                "snapshot_date": "2026-06-30",
                "quantity": "90",
                "warehouse_id": "WH-01",
                "inventory_value": "108.00",
            }
        ],
        "replenishment": [],
    }


def _full_payload() -> dict[str, Any]:
    return {"request": _minimal_request_payload(), "dataset": _minimal_dataset_payload()}


def _validate_contract_payload(payload: dict[str, Any]) -> ValidationReport:
    """统一校验 request + dataset 原始载荷（测试辅助）。

    request 的 pydantic ValidationError 转换为 field=request.<loc> 的 issue；
    dataset 部分委托 validate_raw_dataset。
    """
    issues: list[ValidationIssue] = []
    try:
        AnalysisRequest.model_validate(payload["request"])
    except ValidationError as exc:
        for error in exc.errors():
            loc = ".".join(str(part) for part in error["loc"])
            issues.append(
                ValidationIssue(
                    row=None,
                    field=f"request.{loc}",
                    reason=str(error["msg"]),
                    code=ErrorCode.DATA_VALIDATION_FAILED,
                )
            )
    dataset_report = validate_raw_dataset(payload["dataset"])
    issues.extend(dataset_report.issues)
    return ValidationReport(valid=not issues, issues=issues, warnings=dataset_report.warnings)


def test_minimal_payload_passes_model_and_raw_validation() -> None:
    """合法最小 request + dataset 通过模型校验与 validate_raw_dataset。"""
    request = AnalysisRequest.model_validate(_minimal_request_payload())
    dataset = EngineDataset.model_validate(_minimal_dataset_payload())
    assert request.run_id == "run-contract-0001"
    assert len(dataset.movements) == 1

    report = validate_raw_dataset(dataset.model_dump(mode="json"))
    assert report.valid is True
    assert report.issues == []
    assert report.warnings == []


def test_missing_run_id_located() -> None:
    """缺 run_id：field 定位到 request.run_id，code=DATA_VALIDATION_FAILED。"""
    payload = _full_payload()
    del payload["request"]["run_id"]
    report = _validate_contract_payload(payload)
    assert report.valid is False
    assert len(report.issues) == 1
    issue = report.issues[0]
    assert issue.code is ErrorCode.DATA_VALIDATION_FAILED
    assert issue.field == "request.run_id"


def test_quantity_type_error_located() -> None:
    """quantity 类型错（"abc"）：field 定位到 movements.quantity。"""
    payload = _full_payload()
    payload["dataset"]["movements"][0]["quantity"] = "abc"
    report = _validate_contract_payload(payload)
    assert report.valid is False
    assert len(report.issues) == 1
    issue = report.issues[0]
    assert issue.code is ErrorCode.DATA_VALIDATION_FAILED
    assert issue.field == "movements.quantity"


def test_move_type_enum_invalid_located() -> None:
    """move_type 枚举非法（"LOSS"）：field 定位到 movements.move_type。"""
    payload = _full_payload()
    payload["dataset"]["movements"][0]["move_type"] = "LOSS"
    report = _validate_contract_payload(payload)
    assert report.valid is False
    assert len(report.issues) == 1
    issue = report.issues[0]
    assert issue.code is ErrorCode.DATA_VALIDATION_FAILED
    assert issue.field == "movements.move_type"


def test_quantity_non_positive_located() -> None:
    """quantity 为负/0：field 定位到 movements.quantity。"""
    payload = _full_payload()
    payload["dataset"]["movements"][0]["quantity"] = "-5"
    report = _validate_contract_payload(payload)
    assert report.valid is False
    issue = report.issues[0]
    assert issue.code is ErrorCode.DATA_VALIDATION_FAILED
    assert issue.field == "movements.quantity"


def test_quantity_zero_located() -> None:
    """quantity 为 0 同样被 gt=0 约束拒绝。"""
    payload = _full_payload()
    payload["dataset"]["movements"][0]["quantity"] = "0"
    report = _validate_contract_payload(payload)
    assert report.valid is False
    issue = report.issues[0]
    assert issue.code is ErrorCode.DATA_VALIDATION_FAILED
    assert issue.field == "movements.quantity"


def test_unit_cost_scale_violation_located() -> None:
    """unit_cost scale=3 超过金额上限 2：field 定位到 movements.unit_cost。"""
    payload = _full_payload()
    payload["dataset"]["movements"][0]["unit_cost"] = "9.123"
    report = _validate_contract_payload(payload)
    assert report.valid is False
    assert len(report.issues) == 1
    issue = report.issues[0]
    assert issue.code is ErrorCode.DATA_VALIDATION_FAILED
    assert issue.field == "movements.unit_cost"
