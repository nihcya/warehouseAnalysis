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
    """analyze 正常返回，warnings 含校验层冻结警告与 ANALYSIS_PLACEHOLDER。"""
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


def test_golden_tolerances_declared(expected: dict[str, Any]) -> None:
    """expected.json 声明 M0 容差（指标层数值随 M1 公式实现后补充）。"""
    tolerances = expected["tolerances"]
    assert tolerances["money_abs"] == "0.01"
    assert tolerances["money_rel"] == "1e-6"
    assert tolerances["ratio_abs"] == "1e-9"
    assert tolerances["abc_exact"] is True
    assert "M0" in tolerances["note"]
