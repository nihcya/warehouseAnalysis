"""边界 fixture 参数化测试：记录 M0 当前行为。

- empty：valid 且无 issue/警告；
- negative-stock：NEGATIVE_BALANCE 非阻断警告；
- duplicate-events：DUPLICATE_EVENT 阻断（valid=false）；
- invalid-unit：quantity scale=4 与 unit_cost scale=3 精度违规（阻断）；
- boundary-dates：期间首日合法、end_date 当天触发 PERIOD_MISMATCH（左闭右开）；
- zero-demand / missing-lot：M0 当前行为断言（无阻断、无警告）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from contracts import AnalysisRequest, EngineDataset
from warehouse_engine import WarehouseEngine

EDGE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "edge"

#: (文件名, expected.valid, expected.issue_codes, expected.warning_codes)
EDGE_EXPECTATIONS: list[tuple[str, bool, list[str], list[str]]] = [
    ("empty-dataset.json", True, [], []),
    ("zero-demand.json", True, [], []),
    ("negative-stock.json", True, [], ["NEGATIVE_BALANCE"]),
    ("duplicate-events.json", False, ["DUPLICATE_EVENT"], []),
    ("invalid-unit.json", False, ["DATA_VALIDATION_FAILED", "DATA_VALIDATION_FAILED"], []),
    ("missing-lot.json", True, [], []),
    ("boundary-dates.json", True, [], ["PERIOD_MISMATCH"]),
]


def _load_edge(filename: str) -> dict[str, Any]:
    return json.loads((EDGE_DIR / filename).read_text(encoding="utf-8"))


def _validate_edge(payload: dict[str, Any]):
    request = AnalysisRequest.model_validate(payload["request"])
    dataset = EngineDataset.model_validate(payload["dataset"])
    return WarehouseEngine().validate_dataset(request, dataset)


@pytest.mark.parametrize(
    ("filename", "valid", "issue_codes", "warning_codes"),
    EDGE_EXPECTATIONS,
    ids=[expectation[0] for expectation in EDGE_EXPECTATIONS],
)
def test_edge_fixture_current_behavior(
    filename: str,
    valid: bool,
    issue_codes: list[str],
    warning_codes: list[str],
) -> None:
    report = _validate_edge(_load_edge(filename))

    assert report.valid is valid
    assert [issue.code.value for issue in report.issues] == issue_codes
    assert [w.code for w in report.warnings] == warning_codes


def test_empty_dataset_valid_without_issues() -> None:
    """空数据集：valid 且无 issue（M0 当前行为，不视为失败）。"""
    report = _validate_edge(_load_edge("empty-dataset.json"))
    assert report.valid is True
    assert report.issues == []
    assert report.warnings == []


def test_negative_stock_warns_without_blocking() -> None:
    """负库存快照：NEGATIVE_BALANCE 非阻断警告，字段定位到 snapshots.quantity。"""
    report = _validate_edge(_load_edge("negative-stock.json"))
    assert report.valid is True
    warning = report.warnings[0]
    assert warning.code == "NEGATIVE_BALANCE"
    assert warning.blocking is False
    assert warning.fields == ["quantity"]


def test_duplicate_events_blocks_with_field_location() -> None:
    """重复 event_id：DUPLICATE_EVENT 阻断，field 定位到 movements.event_id、row=1。"""
    report = _validate_edge(_load_edge("duplicate-events.json"))
    assert report.valid is False
    issue = report.issues[0]
    assert issue.code.value == "DUPLICATE_EVENT"
    assert issue.field == "movements.event_id"
    assert issue.row == 1


def test_invalid_unit_precision_issues_located() -> None:
    """精度违规：quantity scale=4 与 unit_cost scale=3 分别定位到具体字段。"""
    report = _validate_edge(_load_edge("invalid-unit.json"))
    assert report.valid is False
    fields = {issue.field for issue in report.issues}
    assert fields == {"movements.quantity", "movements.unit_cost"}
    assert all(issue.code.value == "DATA_VALIDATION_FAILED" for issue in report.issues)


def test_boundary_dates_period_mismatch_on_end_date_only() -> None:
    """期间首日（2026-06-01）合法；end_date 当天（2026-06-30）触发 PERIOD_MISMATCH。"""
    report = _validate_edge(_load_edge("boundary-dates.json"))
    assert report.valid is True
    assert len(report.warnings) == 1
    warning = report.warnings[0]
    assert warning.code == "PERIOD_MISMATCH"
    assert warning.blocking is False
    # 仅 end_date 当天的 movement（row=1）触发，首日（row=0）不触发
    assert "movements[1]" in warning.message


def test_zero_demand_current_behavior() -> None:
    """M0 当前行为：avg_daily_demand=0 通过模型（ge=0）且不触发任何规则。"""
    payload = _load_edge("zero-demand.json")
    report = _validate_edge(payload)
    assert report.valid is True
    assert report.issues == []
    assert report.warnings == []
    assert payload["dataset"]["replenishment"][0]["avg_daily_demand"] == "0"


def test_missing_lot_current_behavior() -> None:
    """M0 当前行为：无 lot_id 不阻断（M0 记录该行为，批次规则待 M2 冻结）。"""
    payload = _load_edge("missing-lot.json")
    report = _validate_edge(payload)
    assert report.valid is True
    assert report.issues == []
    assert report.warnings == []
    assert all("lot_id" not in movement for movement in payload["dataset"]["movements"])
