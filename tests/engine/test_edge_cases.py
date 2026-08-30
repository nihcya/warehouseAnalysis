"""边界 fixture 参数化测试：校验层（M0 冻结）与指标层（M1 补充）。

M0 校验层断言（不因 M1 扩展而破坏）：

- empty：valid 且无 issue/警告；
- negative-stock：NEGATIVE_BALANCE 非阻断警告；
- duplicate-events：DUPLICATE_EVENT 阻断（valid=false）；
- invalid-unit：quantity scale=4 与 unit_cost scale=3 精度违规（阻断）；
- boundary-dates：期间首日合法、end_date 当天触发 PERIOD_MISMATCH（左闭右开）；
- zero-demand / missing-lot：M0 当前行为断言（无阻断、无警告）。

M1 指标层断言（Task 4）：各 fixture JSON 内的 expected 段冻结手工推导的
指标数值与 Warning 序列，按 formula-spec §10 容差执行；校验阻断的 fixture
（blocked=true）断言 analyze 抛 DataValidationError、不产出指标。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from contracts import AnalysisRequest, EngineDataset
from warehouse_engine import WarehouseEngine
from warehouse_engine.errors import DataValidationError

from tests.engine.conftest import ALL_M2_FORMULA_IDS

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

#: 携带 M1 指标层预期（expected 段）的全部边界 fixture
EDGE_METRIC_FIXTURES = [
    "empty-dataset.json",
    "zero-demand.json",
    "negative-stock.json",
    "duplicate-events.json",
    "invalid-unit.json",
    "missing-lot.json",
    "boundary-dates.json",
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


@pytest.mark.parametrize(
    "filename",
    EDGE_METRIC_FIXTURES,
    ids=EDGE_METRIC_FIXTURES,
)
def test_edge_fixture_analyze_metrics_match_expected(
    filename: str,
    tolerance_check,
) -> None:
    """指标层：analyze 结果与 fixture expected 段的手工推导冻结值一致。

    - blocked=true：校验阻断，analyze 抛 DataValidationError（无指标输出）；
    - 其余：M2 起 analyze 恒输出 18 个指标（五类公式，每个 formula_id 恰一个）；
      fixture 的 expected["metrics"] 冻结其中手工推导的部分（M1 冻结的 9 个
      KPI/COGS 指标必含在内），按 §10 容差断言（null 值含 reason）；
      Warning 序列（code + fields）与冻结值完全一致。
    """
    payload = _load_edge(filename)
    expected = payload["expected"]
    request = AnalysisRequest.model_validate(payload["request"])
    dataset = EngineDataset.model_validate(payload["dataset"])

    if expected.get("blocked"):
        with pytest.raises(DataValidationError):
            WarehouseEngine().analyze(request, dataset)
        return

    result = WarehouseEngine().analyze(request, dataset)

    by_id = {metric.formula_id: metric for metric in result.metrics}
    # M2：指标清单恒为 18 个；fixture 只冻结其中手工推导的部分
    assert set(by_id) == set(ALL_M2_FORMULA_IDS)
    assert set(expected["metrics"]) <= set(by_id)
    for formula_id, item in expected["metrics"].items():
        metric = by_id[formula_id]
        assert metric.unit == item["unit"], formula_id
        assert metric.sample_count == item["sample_count"], formula_id
        if "reason" in item:
            assert metric.reason == item["reason"], formula_id
        else:
            assert metric.reason is None, formula_id
        tolerance_check(item["value"], str(metric.value), item["tolerance"])

    assert [(w.code, w.fields) for w in result.warnings] == [
        (item["code"], item["fields"]) for item in expected["analyze_warnings"]
    ]


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
