"""黄金数据测试：golden input 的校验与占位分析结果对照 expected.json 冻结值。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from contracts import AnalysisRequest, EngineDataset
from warehouse_engine import WarehouseEngine

GOLDEN_DIR = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "golden" / "v0.1.0"
)


@pytest.fixture(scope="module")
def golden_payload() -> dict[str, Any]:
    return json.loads((GOLDEN_DIR / "input.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def expected() -> dict[str, Any]:
    return json.loads((GOLDEN_DIR / "expected.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def analysis_request(golden_payload: dict[str, Any]) -> AnalysisRequest:
    return AnalysisRequest.model_validate(golden_payload["request"])


@pytest.fixture(scope="module")
def dataset(golden_payload: dict[str, Any]) -> EngineDataset:
    return EngineDataset.model_validate(golden_payload["dataset"])


def test_golden_validation_matches_expected(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
) -> None:
    """validate_dataset 结果与 expected.json 的冻结值一致。"""
    report = WarehouseEngine().validate_dataset(analysis_request, dataset)

    assert report.valid == expected["validation"]["valid"]
    assert [issue.code.value for issue in report.issues] == expected["validation"]["issue_codes"]
    assert [w.code for w in report.warnings] == expected["validation"]["warning_codes"]


def test_golden_analyze_contains_placeholder_warning(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
) -> None:
    """analyze 正常返回，warnings 含校验层冻结警告与 ANALYSIS_PLACEHOLDER。

    M1 起 ANALYSIS_PLACEHOLDER 仅表示 abc-aging/replenishment/forecasting/
    benchmark 四类计算器为占位（KPI/COGS 已返回真实指标）。
    """
    result = WarehouseEngine().analyze(analysis_request, dataset)

    codes = [w.code for w in result.warnings]
    assert "ANALYSIS_PLACEHOLDER" in codes
    for code in expected["validation"]["warning_codes"]:
        assert code in codes

    assert result.run_id == analysis_request.run_id
    assert result.engine_version == "0.1.0"
    assert result.formula_version == "0.1.0"
    assert result.input_summary.movement_count == len(dataset.movements)
    assert result.input_summary.snapshot_count == len(dataset.snapshots)


#: M1 黄金指标值（golden input 手工推导冻结；expected.json 容差见 §10）
GOLDEN_METRICS: dict[str, tuple[str, str, int]] = {
    "F-KPI-001": ("0", "件", 3),  # 无历史事件
    "F-KPI-002": ("162.5", "件", 3),  # 73 + 60 + 29.5（SKU-0002 重放期末为 60，快照 -5 仅触发警告）
    "F-KPI-003": ("67.5", "件", 3),  # (30+2−5) + 20 + (15+5.5)，调拨不计入
    "F-KPI-004": ("1.000000", "ratio", 3),
    "F-KPI-005": ("356.60", "CNY", 3),  # 87.60 + 210.00 + 59.00
    "F-COGS-001": ("143.40", "CNY", 3),  # 32.40 + 70.00 + 41.00（移动加权平均）
    "F-KPI-006": ("0.804262", "次/期间", 3),  # 143.40 / ((0+356.60)/2)
    "F-KPI-007": ("36.057880", "天", 3),  # 29 / turnover
    "F-KPI-008": ("69.814815", "天", 3),  # 162.5 / (67.5/29)
}


def test_golden_analyze_real_kpi_metrics(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
) -> None:
    """M1：analyze 返回真实 KPI/COGS 指标，数值与手工推导的冻结值一致。"""
    result = WarehouseEngine().analyze(analysis_request, dataset)

    assert len(result.metrics) == len(GOLDEN_METRICS)
    by_id = {metric.formula_id: metric for metric in result.metrics}
    assert set(by_id) == set(GOLDEN_METRICS)
    for formula_id, (value, unit, sample_count) in GOLDEN_METRICS.items():
        metric = by_id[formula_id]
        assert metric.value == value, formula_id
        assert metric.unit == unit, formula_id
        assert metric.sample_count == sample_count, formula_id
        assert metric.formula_version == "0.1.0"
        assert metric.reason is None, formula_id


def test_golden_analyze_data_quality_report(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
) -> None:
    """M1：数据质量报告按码汇总校验层冻结警告 + 占位警告。"""
    result = WarehouseEngine().analyze(analysis_request, dataset)

    assert result.data_quality is not None
    entries = {entry.code: entry for entry in result.data_quality}
    assert set(entries) == set(expected["validation"]["warning_codes"]) | {"ANALYSIS_PLACEHOLDER"}
    for code, entry in entries.items():
        matching = [w for w in result.warnings if w.code == code]
        assert entry.count == len(matching) == 1
        assert entry.details == matching


def test_golden_tolerances_declared(expected: dict[str, Any]) -> None:
    """expected.json 声明 M0 容差（指标层数值随 M1 公式实现后补充）。"""
    tolerances = expected["tolerances"]
    assert tolerances["money_abs"] == "0.01"
    assert tolerances["money_rel"] == "1e-6"
    assert tolerances["ratio_abs"] == "1e-9"
    assert tolerances["abc_exact"] is True
    assert "M0" in tolerances["note"]
