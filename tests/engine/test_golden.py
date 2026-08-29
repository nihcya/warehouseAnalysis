"""黄金数据测试：golden input 的校验层与指标层对照 expected.json 冻结值。

数据集版本（input.json + expected.json 成对，均为手工推导冻结）：

- v0.1.0：M0 冻结校验层，M1 扩展指标层——正常周转主路径（空历史期初 0、
  期内盘点替换、退货冲减、报废、调拨不计入出库量、end_date 当天事件不计入）；
- v0.2.0：M1 补充数据集——历史盘点与历史冲销构成的期初（25）、期末为 0
  与期末为负（NEGATIVE_BALANCE + 该 SKU COGS 置零）、净退货出库量为负、
  期内冲销、多次入库不同成本的移动加权 COGS、UNIT_COST_MISSING 的快照
  回退与价值 null 项（不计入合计）。

expected.json 的全部数值按 formula-spec §3 手工推导写入（derivation 字段
留有推导过程），禁止以引擎输出回填（防自证）；数值断言按 §10 容差执行
（容差表见 tests/engine/conftest.py）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from contracts import AnalysisRequest, EngineDataset
from warehouse_engine import WarehouseEngine
from warehouse_engine.calculators import inventory_kpi

GOLDEN_ROOT = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "golden"
)

#: 黄金数据集版本（目录名，input.json + expected.json 成对）
GOLDEN_VERSIONS = ["v0.1.0", "v0.2.0"]


@pytest.fixture(params=GOLDEN_VERSIONS)
def golden_dir(request: pytest.FixtureRequest) -> Path:
    """黄金数据集目录（参数化覆盖全部版本）。"""
    return GOLDEN_ROOT / request.param


@pytest.fixture
def golden_payload(golden_dir: Path) -> dict[str, Any]:
    return json.loads((golden_dir / "input.json").read_text(encoding="utf-8"))


@pytest.fixture
def expected(golden_dir: Path) -> dict[str, Any]:
    return json.loads((golden_dir / "expected.json").read_text(encoding="utf-8"))


@pytest.fixture
def analysis_request(golden_payload: dict[str, Any]) -> AnalysisRequest:
    return AnalysisRequest.model_validate(golden_payload["request"])


@pytest.fixture
def dataset(golden_payload: dict[str, Any]) -> EngineDataset:
    return EngineDataset.model_validate(golden_payload["dataset"])


def test_golden_validation_matches_expected(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
) -> None:
    """validate_dataset 结果与 expected.json 的校验层冻结值一致。"""
    report = WarehouseEngine().validate_dataset(analysis_request, dataset)

    assert report.valid == expected["validation"]["valid"]
    assert [issue.code.value for issue in report.issues] == expected["validation"]["issue_codes"]
    assert [w.code for w in report.warnings] == expected["validation"]["warning_codes"]


def test_golden_analyze_metadata_and_placeholder_warning(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
) -> None:
    """analyze 正常返回：占位警告在列，版本与输入摘要可追溯。

    M1 起 ANALYSIS_PLACEHOLDER 仅表示 abc-aging/replenishment/forecasting/
    benchmark 四类计算器为占位（KPI/COGS 已返回真实指标）。
    """
    result = WarehouseEngine().analyze(analysis_request, dataset)

    codes = [w.code for w in result.warnings]
    assert "ANALYSIS_PLACEHOLDER" in codes
    for code in expected["validation"]["warning_codes"]:
        assert code in codes

    assert result.run_id == analysis_request.run_id
    assert result.engine_version == "0.2.0"  # 引擎实现版本随包演进（M1 为 0.2.0），公式口径仍冻结 0.1.0
    assert result.formula_version == "0.1.0"
    assert result.input_summary.movement_count == len(dataset.movements)
    assert result.input_summary.snapshot_count == len(dataset.snapshots)


def test_golden_analyze_real_kpi_metrics(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
    tolerance_check,
) -> None:
    """M1：analyze 返回真实 KPI/COGS 指标，数值与手工推导冻结值一致（§10 容差）。"""
    result = WarehouseEngine().analyze(analysis_request, dataset)

    expected_metrics = expected["metrics"]
    by_id = {metric.formula_id: metric for metric in result.metrics}
    assert len(result.metrics) == len(expected_metrics)
    assert set(by_id) == set(expected_metrics) == set(inventory_kpi.FORMULA_IDS)
    for formula_id, item in expected_metrics.items():
        metric = by_id[formula_id]
        assert metric.unit == item["unit"], formula_id
        assert metric.sample_count == item["sample_count"], formula_id
        assert metric.formula_version == "0.1.0", formula_id
        assert metric.reason is None, formula_id
        tolerance_check(item["value"], str(metric.value), item["tolerance"])


def test_golden_per_sku_metrics(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
    tolerance_check,
) -> None:
    """SKU 级明细（重放 + 汇总口径）与手工推导冻结值一致（含价值 null 项）。"""
    result = inventory_kpi.calculate(analysis_request, dataset)

    per_sku = {kpi.sku_id: kpi for kpi in result.per_sku}
    assert set(per_sku) == set(expected["per_sku"])
    for sku_id, item in expected["per_sku"].items():
        kpi = per_sku[sku_id]
        tolerance_check(item["opening_qty"], str(kpi.opening_qty), "qty")
        tolerance_check(item["closing_qty"], str(kpi.closing_qty), "qty")
        tolerance_check(item["out_qty"], str(kpi.out_qty), "qty")
        tolerance_check(item["cogs"], str(kpi.cogs), "money")
        tolerance_check(
            item["inventory_value"],
            None if kpi.inventory_value is None else str(kpi.inventory_value),
            "money",
        )


def test_golden_analyze_warnings_match_expected(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
) -> None:
    """analyze 的 Warning（code + fields，按序）与 expected 冻结值完全一致（§10）。"""
    result = WarehouseEngine().analyze(analysis_request, dataset)

    assert [(w.code, w.fields) for w in result.warnings] == [
        (item["code"], item["fields"]) for item in expected["analyze_warnings"]
    ]


def test_golden_analyze_data_quality_report(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
) -> None:
    """数据质量报告按码汇总校验层 + 计算层警告（计数 + 明细），与 warnings 对应。"""
    result = WarehouseEngine().analyze(analysis_request, dataset)

    assert result.data_quality is not None
    entries = {entry.code: entry for entry in result.data_quality}
    expected_codes = {item["code"] for item in expected["analyze_warnings"]}
    assert set(entries) == expected_codes
    for code, entry in entries.items():
        matching = [w for w in result.warnings if w.code == code]
        assert entry.count == len(matching), code
        assert entry.details == matching, code
    # 条目按 code 字典序排列（确定性）
    codes = [entry.code for entry in result.data_quality]
    assert codes == sorted(codes)


def test_golden_tolerances_declared(expected: dict[str, Any]) -> None:
    """expected.json 按 formula-spec §10 声明指标层容差总表。"""
    tolerances = expected["tolerances"]
    assert tolerances["money_abs"] == "0.01"
    assert tolerances["money_rel"] == "1e-6"
    assert tolerances["qty_abs"] == "0.001"
    assert tolerances["qty_rel"] == "1e-9"
    assert tolerances["ratio_abs"] == "1e-6"
    assert tolerances["ratio_rel"] == "1e-6"
    assert tolerances["abc_exact"] is True
