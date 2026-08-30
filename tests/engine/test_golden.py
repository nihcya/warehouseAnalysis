"""黄金数据测试：golden input 的校验层与指标层对照 expected.json 冻结值。

数据集版本（input.json + expected.json 成对，均为手工推导冻结）：

- v0.1.0：M0 冻结校验层，M1 扩展指标层——正常周转主路径（空历史期初 0、
  期内盘点替换、退货冲减、报废、调拨不计入出库量、end_date 当天事件不计入）。
  M2 补入结构性可推导的 F-ABC-001 与 F-BM-001；AGE/STALE/FCST/REPL 的数值
  口径由 v0.3.0 / v0.4.0 冻结（见各 expected.json 的 frozen_scope）。
- v0.2.0：M1 新增数据集——历史盘点与历史冲销构成的期初（25）、期末为 0
  与期末为负（NEGATIVE_BALANCE + 该 SKU COGS 置零）、净退货出库量为负、
  期内冲销、多次入库不同成本的移动加权 COGS、UNIT_COST_MISSING 的快照
  回退与价值 null 项（不计入合计）。
- **v0.3.0（M2）**：ABC / 库龄 / 呆滞焦点，冻结全部 18 项指标——累计占比
  0.40/0.70/0.90/0.98/1.00 的 A/A/B/C/C 归属与 amt=0 归 C、批次与 FIFO 残余
  双口径的五个库龄档位（0-30 / 30-60 / 60-90 / 90-180 / 180+）、呆滞
  no_outflow_days = 171 > 90 判定与新品豁免分界。
- **v0.4.0（M2）**：补货 / 预测与误差 / 基准焦点，冻结全部 18 项指标——
  σ_d = 0 → SS = 0 边界与非恒定需求的数值路径（σ_d ≈ 15.257924）、
  ROP/Q* 与在途扣减、自然周聚合的滚动预测与合并 MAPE = 25、基准命中后
  相对偏差 −0.173913。

expected.json 的全部数值按 formula-spec 手工推导写入（derivation 字段
留有推导过程），禁止以引擎输出回填（防自证）；数值断言按 §10 容差执行
（容差表见 tests/engine/conftest.py）。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from contracts import AnalysisRequest, EngineDataset
from warehouse_engine import WarehouseEngine
from warehouse_engine.calculators import (
    abc_aging,
    benchmark_compare,
    forecasting,
    inventory_kpi,
    replenishment,
)
from warehouse_engine.replay import replay_movements

from tests.engine.conftest import ALL_M2_FORMULA_IDS

GOLDEN_ROOT = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "golden"
)

#: 黄金数据集版本（目录名，input.json + expected.json 成对）
GOLDEN_VERSIONS = ["v0.1.0", "v0.2.0", "v0.3.0", "v0.4.0"]


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


def test_golden_analyze_metadata_and_no_placeholder(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
) -> None:
    """analyze 版本与输入摘要可追溯；M2 起不再出现 ANALYSIS_PLACEHOLDER。"""
    result = WarehouseEngine().analyze(analysis_request, dataset)

    codes = [w.code for w in result.warnings]
    assert "ANALYSIS_PLACEHOLDER" not in codes
    for code in expected["validation"]["warning_codes"]:
        assert code in codes

    assert result.run_id == analysis_request.run_id
    assert result.engine_version == "0.3.0"  # M2 引擎实现版本；公式口径仍冻结 0.1.0
    assert result.formula_version == "0.1.0"
    assert result.input_summary.movement_count == len(dataset.movements)
    assert result.input_summary.snapshot_count == len(dataset.snapshots)


def test_golden_metric_inventory_is_complete(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
) -> None:
    """M2 后 analyze 恒输出 18 个指标，每个 formula_id 恰一个。"""
    result = WarehouseEngine().analyze(analysis_request, dataset)

    formula_ids = [metric.formula_id for metric in result.metrics]
    assert len(formula_ids) == len(set(formula_ids)) == len(ALL_M2_FORMULA_IDS)
    assert set(formula_ids) == set(ALL_M2_FORMULA_IDS)


def test_golden_frozen_metrics_match_expected(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
    tolerance_check,
) -> None:
    """expected.json 冻结的每个指标与手工推导值一致（§10 容差）。

    各版本冻结范围不同（见 expected.json 的 frozen_scope）：v0.1.0/v0.2.0 冻结
    KPI 层与结构性可推导项，v0.3.0/v0.4.0 冻结全部 18 项。
    """
    result = WarehouseEngine().analyze(analysis_request, dataset)

    by_id = {metric.formula_id: metric for metric in result.metrics}
    for formula_id, item in expected["metrics"].items():
        metric = by_id[formula_id]
        assert metric.unit == item["unit"], formula_id
        assert metric.sample_count == item["sample_count"], formula_id
        assert metric.formula_version == "0.1.0", formula_id
        assert metric.reason == item.get("reason"), formula_id
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


# --- M2 明细断言（仅对声明了相应 data 段的黄金版本生效）---


def test_golden_abc_classification_matches_expected(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
) -> None:
    """ABC 分类与排序必须与期望完全一致（§10：分类、排序序号完全一致）。"""
    if "abc" not in expected:
        pytest.skip("该黄金版本未冻结 ABC 明细")

    result = abc_aging.calculate(analysis_request, dataset)
    actual = [
        {
            "rank": row.rank,
            "sku_id": row.sku_id,
            "amt": str(row.amt),
            "cum_ratio": str(row.cum_ratio),
            "abc_class": row.abc_class,
        }
        for row in result.abc_rows
    ]
    for actual_row, expected_row in zip(actual, expected["abc"]["order"], strict=True):
        assert actual_row["rank"] == expected_row["rank"]
        assert actual_row["sku_id"] == expected_row["sku_id"]
        assert Decimal(actual_row["amt"]) == Decimal(expected_row["amt"])
        assert Decimal(actual_row["cum_ratio"]).quantize(Decimal("0.000001")) == Decimal(
            expected_row["cum_ratio"]
        ).quantize(Decimal("0.000001"))
        assert actual_row["abc_class"] == expected_row["abc_class"]


def test_golden_aging_distribution_matches_expected(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
) -> None:
    """库龄档位归属与各档数量/金额分布与期望完全一致（§10：档位完全一致）。"""
    if "aging" not in expected:
        pytest.skip("该黄金版本未冻结库龄明细")

    result = abc_aging.calculate(analysis_request, dataset)
    actual = {
        (row.sku_id, row.lot_id): (str(row.qty), str(row.age_days), row.bucket, row.age_basis)
        for row in result.aging_rows
    }
    for expected_row in expected["aging"]["rows"]:
        key = (expected_row["sku_id"], expected_row["lot_id"])
        assert key in actual, key
        qty, age, bucket, basis = actual[key]
        assert Decimal(qty) == Decimal(expected_row["qty"]), key
        assert Decimal(age) == Decimal(expected_row["age_days"]), key
        assert bucket == expected_row["bucket"], key
        assert basis == expected_row["age_basis"], key

    for bucket, qty in expected["aging"]["bucket_qty"].items():
        assert result.bucket_qty[bucket] == Decimal(qty), bucket
    for bucket, amount in expected["aging"]["bucket_amount"].items():
        assert result.bucket_amount[bucket] == Decimal(amount), bucket


def test_golden_stale_verdicts_match_expected(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
) -> None:
    """呆滞判定结论与 excluded_reason 与期望完全一致（§10：结论完全一致）。"""
    if "stale" not in expected:
        pytest.skip("该黄金版本未冻结呆滞明细")

    result = abc_aging.calculate(analysis_request, dataset)
    actual = {
        row.sku_id: (row.no_outflow_days, row.is_stale, row.excluded_reason)
        for row in result.stale_rows
    }
    for expected_row in expected["stale"]["rows"]:
        sku_id = expected_row["sku_id"]
        assert sku_id in actual, sku_id
        days, is_stale, reason = actual[sku_id]
        assert days == expected_row["no_outflow_days"], sku_id
        assert is_stale == expected_row["is_stale"], sku_id
        assert reason == expected_row["excluded_reason"], sku_id

    assert result.stale_qty == Decimal(expected["stale"]["stale_qty"])
    if expected["stale"]["stale_ratio"] is None:
        assert result.stale_ratio is None
    else:
        assert result.stale_ratio == Decimal(expected["stale"]["stale_ratio"])


def test_golden_replenishment_matches_expected(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
    tolerance_check,
) -> None:
    """补货 SS/ROP/Q* 与所用 z 值与期望一致（§10 数量类容差）。"""
    if "replenishment" not in expected:
        pytest.skip("该黄金版本未冻结补货明细")

    result = replenishment.calculate(analysis_request, dataset)
    actual = {row.sku_id: row for row in result.per_sku}
    for expected_row in expected["replenishment"]["rows"]:
        row = actual[expected_row["sku_id"]]
        if row.z is None:
            assert expected_row["z"] is None
        else:
            tolerance_check(expected_row["z"], str(row.z), "qty")
        tolerance_check(expected_row["safety_stock"], str(row.safety_stock), "qty")
        tolerance_check(expected_row["reorder_point"], str(row.reorder_point), "qty")
        tolerance_check(expected_row["suggested_qty"], str(row.suggested_qty), "qty")
        tolerance_check(expected_row["sigma_d"], str(row.sigma_d), "qty")
        tolerance_check(expected_row["avg_daily_demand"], str(row.avg_daily_demand), "qty")
        assert list(row.missing_params) == expected_row["missing_params"]


def test_golden_forecast_matches_expected(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
    tolerance_check,
) -> None:
    """逐周需求、下一周预测与逐 SKU 误差指标与期望一致。"""
    if "forecast" not in expected:
        pytest.skip("该黄金版本未冻结预测明细")

    result = forecasting.calculate(analysis_request, dataset)
    assert result.split_date.isoformat() == expected["forecast"]["split_date"]

    actual = {row.sku_id: row for row in result.per_sku}
    for expected_row in expected["forecast"]["rows"]:
        row = actual[expected_row["sku_id"]]
        assert [str(week.demand) for week in row.weekly_demand] == expected_row["weekly_demand"]
        assert row.train_periods == expected_row["train_periods"]
        assert row.forecast_periods == expected_row["forecast_periods"]
        tolerance_check(expected_row["next_week_forecast"], str(row.next_week_forecast), "qty")
        tolerance_check(expected_row["daily_forecast"], str(row.daily_forecast), "qty")
        tolerance_check(expected_row["mape"], str(row.mape), "ratio")
        tolerance_check(expected_row["wape"], str(row.wape), "ratio")
        tolerance_check(expected_row["mae"], str(row.mae), "ratio")
        tolerance_check(expected_row["rmse"], str(row.rmse), "ratio")


def test_golden_benchmark_matches_expected(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
    expected: dict[str, Any],
    tolerance_check,
) -> None:
    """基准命中结果、完整元数据与相对偏差与期望一致。"""
    if "benchmark" not in expected:
        pytest.skip("该黄金版本未冻结基准明细")

    kpi = inventory_kpi.calculate(analysis_request, dataset)
    result = benchmark_compare.calculate(analysis_request, dataset, kpi_metrics=kpi.metrics)

    assert result.discarded_records == expected["benchmark"]["discarded_records"]
    actual = {item.metric_name: item for item in result.comparisons}
    for expected_row in expected["benchmark"]["comparisons"]:
        item = actual[expected_row["metric_name"]]
        tolerance_check(expected_row["merchant_value"], str(item.merchant_value), "ratio")
        tolerance_check(expected_row["benchmark_value"], str(item.benchmark_value), "ratio")
        tolerance_check(expected_row["relative_diff"], str(item.relative_diff), "ratio")
        assert item.benchmark.source == expected_row["source"]
        assert item.benchmark.region == expected_row["region"]
        assert item.benchmark.industry == expected_row["industry"]
        assert item.benchmark.benchmark_version == expected_row["benchmark_version"]
        assert item.benchmark.updated_at.isoformat() == expected_row["updated_at"]
        assert item.benchmark.unit == expected_row["unit"]


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


def test_golden_analyze_is_deterministic(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
) -> None:
    """同一黄金输入两次 analyze：序列化结果逐字节一致。"""
    engine = WarehouseEngine()
    first = engine.analyze(analysis_request, dataset)
    second = engine.analyze(analysis_request, dataset)

    assert first.model_dump_json() == second.model_dump_json()


def test_golden_replay_is_reusable_across_calculators(
    analysis_request: AnalysisRequest,
    dataset: EngineDataset,
) -> None:
    """重放内核单次执行的结果可注入各计算器，与各自内部重放等价。"""
    outcome = replay_movements(analysis_request, dataset.movements)

    direct = abc_aging.calculate(analysis_request, dataset)
    injected = abc_aging.calculate(analysis_request, dataset, outcome=outcome)
    assert [row.abc_class for row in direct.abc_rows] == [
        row.abc_class for row in injected.abc_rows
    ]
