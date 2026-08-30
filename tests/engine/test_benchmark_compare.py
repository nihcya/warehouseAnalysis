"""benchmark_compare 计算器测试（F-BM-001，formula-spec §9）。

用例覆盖 formula-spec §9 声明的黄金数据两组（有匹配 / 无匹配）与必备字段校验：

- 有匹配：按 ``(industry, region, metric)`` 命中，输出商户值、基准值、绝对与
  相对差异，并带回基准完整八字段元数据；
- 多条命中取 ``benchmark_version`` 与 ``updated_at`` 最新者，并列按 ``source``
  字典序；
- 必备字段缺失的记录被丢弃（绝不作为兜底值使用）；
- 无匹配 → 非阻断 ``BENCHMARK_UNAVAILABLE``，不输出任何比较结论。

基准数据集为版本化 fixture ``tests/fixtures/benchmarks/v0.1.0.json``，由测试
载入后经 ``request.parameters["benchmarks"]`` 注入（引擎不读文件）。
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from contracts import AnalysisRequest, EngineDataset, ResultMetric
from warehouse_engine.calculators import benchmark_compare

BENCHMARK_FIXTURE = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "benchmarks" / "v0.1.0.json"
)


def _request(**parameters: object) -> AnalysisRequest:
    return AnalysisRequest(
        run_id="run-benchmark",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 30),
        warehouse_ids=["WH-01"],
        parameters=dict(parameters),
    )


def _benchmarks() -> list[dict[str, Any]]:
    payload = json.loads(BENCHMARK_FIXTURE.read_text(encoding="utf-8"))
    return list(payload["records"])


def _metric(name: str, value: str, unit: str = "次/期间") -> ResultMetric:
    return ResultMetric(
        name=name,
        value=value,
        unit=unit,
        formula_id="F-KPI-006",
        formula_version="0.1.0",
        sample_count=10,
    )


# --- 有匹配 -----------------------------------------------------------------


def test_matched_benchmark_produces_comparison_with_metadata() -> None:
    """命中后输出商户值、基准值、绝对/相对差异与基准完整元数据（§9）。"""
    result = benchmark_compare.calculate(
        _request(industry="综合零售", benchmarks=_benchmarks()),
        EngineDataset(),
        kpi_metrics=[_metric("KPI.TURNOVER", "6.75")],
    )

    assert len(result.comparisons) == 1
    comparison = result.comparisons[0]
    assert comparison.metric_name == "KPI.TURNOVER"
    assert comparison.merchant_value == Decimal("6.75")
    assert comparison.benchmark_value == Decimal("4.50")  # 1.1.0 为最新版本
    assert comparison.absolute_diff == Decimal("2.25")
    assert comparison.relative_diff == Decimal("0.5")

    metadata = comparison.benchmark
    assert metadata.source == "中国仓储与配送协会"
    assert metadata.region == "全国"
    assert metadata.industry == "综合零售"
    assert metadata.sample_scope
    assert metadata.updated_at == date(2026, 3, 1)
    assert metadata.benchmark_version == "1.1.0"
    assert metadata.unit == "次/期间"
    assert metadata.applicability


def test_latest_version_and_updated_at_wins() -> None:
    """多条命中取 benchmark_version 与 updated_at 最新者（1.0.0@01-15 → 1.1.0@03-01）。"""
    result = benchmark_compare.calculate(
        _request(industry="综合零售", benchmarks=_benchmarks()),
        EngineDataset(),
        kpi_metrics=[_metric("KPI.TURNOVER", "5.00")],
    )

    assert result.comparisons[0].benchmark.benchmark_version == "1.1.0"
    assert result.comparisons[0].benchmark.value == Decimal("4.50")


def test_region_explicit_match_overrides_national() -> None:
    """显式指定 region=华东 时命中该地区基准（5.20），而非全国口径（4.50）。"""
    result = benchmark_compare.calculate(
        _request(industry="综合零售", region="华东", benchmarks=_benchmarks()),
        EngineDataset(),
        kpi_metrics=[_metric("KPI.TURNOVER", "5.00")],
    )

    assert result.comparisons[0].benchmark.region == "华东"
    assert result.comparisons[0].benchmark_value == Decimal("5.20")


def test_deviation_ratio_is_mean_of_relative_diffs() -> None:
    """BM.DEVIATION_RATIO 为各比较项相对偏差的算术平均（跨单位不可求和）。"""
    result = benchmark_compare.calculate(
        _request(industry="综合零售", benchmarks=_benchmarks()),
        EngineDataset(),
        kpi_metrics=[
            _metric("KPI.TURNOVER", "6.75"),  # (6.75−4.50)/4.50 = 0.5
            _metric("KPI.TURNOVER_DAYS", "3.50", unit="天"),  # (3.50−7.00)/7.00 = −0.5
        ],
    )

    metric = result.metrics[0]
    assert metric.value == "0.000000"  # 均值 (0.5 + (−0.5)) / 2 = 0
    assert metric.sample_count == 2


# --- 无匹配与字段校验 -------------------------------------------------------


def test_no_match_emits_benchmark_unavailable() -> None:
    """无匹配基准 → 非阻断 BENCHMARK_UNAVAILABLE，不输出任何比较结论。"""
    result = benchmark_compare.calculate(
        _request(industry="医疗器械", benchmarks=_benchmarks()),
        EngineDataset(),
        kpi_metrics=[_metric("KPI.TURNOVER", "6.75")],
    )

    assert result.comparisons == ()
    assert [(w.code, w.fields, w.blocking) for w in result.warnings] == [
        ("BENCHMARK_UNAVAILABLE", ["医疗器械", "全国"], False)
    ]
    metric = result.metrics[0]
    assert metric.value == "null"
    assert metric.reason == "benchmark_unavailable"


def test_no_benchmarks_configured_emits_unavailable() -> None:
    """未注入任何基准数据时同样返回 BENCHMARK_UNAVAILABLE（不做经验值兜底）。"""
    result = benchmark_compare.calculate(
        _request(industry="综合零售"),
        EngineDataset(),
        kpi_metrics=[_metric("KPI.TURNOVER", "6.75")],
    )

    assert result.comparisons == ()
    assert [w.code for w in result.warnings] == ["BENCHMARK_UNAVAILABLE"]


def test_records_missing_required_fields_are_discarded() -> None:
    """必备字段缺失的记录被丢弃，且不作为兜底值参与匹配（§9）。"""
    result = benchmark_compare.calculate(
        _request(industry="综合零售", benchmarks=_benchmarks()),
        EngineDataset(),
        kpi_metrics=[_metric("KPI.TURNOVER", "6.75")],
    )

    # fixture 中两条不合规记录被丢弃：缺 metric 字段、applicability 为空
    assert result.discarded_records == 2
    compared = {item.metric_name for item in result.comparisons}
    assert compared == {"KPI.TURNOVER"}


def test_blank_applicability_record_discarded() -> None:
    """applicability 为空字符串的记录同样被丢弃（必备字段不得为空）。"""
    result = benchmark_compare.calculate(
        _request(industry="综合零售", benchmarks=_benchmarks()),
        EngineDataset(),
        kpi_metrics=[_metric("KPI.COGS", "1200.00", unit="CNY")],
    )

    # 该基准记录的 applicability 为空，被丢弃后 KPI.COGS 无可用基准
    assert result.discarded_records == 2
    assert result.comparisons == ()


# --- 边界与契约 -------------------------------------------------------------


def test_zero_benchmark_value_yields_null_relative_diff() -> None:
    """基准值为 0 时相对差异无定义 → relative_diff 为 None，该比较项不进入均值。"""
    benchmarks = [
        {
            "source": "测试来源",
            "region": "全国",
            "industry": "综合零售",
            "sample_scope": "测试样本",
            "updated_at": "2026-01-01",
            "benchmark_version": "1.0.0",
            "unit": "次/期间",
            "applicability": "仅测试",
            "metric": "KPI.TURNOVER",
            "value": "0",
        }
    ]
    result = benchmark_compare.calculate(
        _request(industry="综合零售", benchmarks=benchmarks),
        EngineDataset(),
        kpi_metrics=[_metric("KPI.TURNOVER", "6.75")],
    )

    assert result.comparisons[0].relative_diff is None
    metric = result.metrics[0]
    assert metric.value == "null"
    assert metric.reason == "zero_benchmark_value"


def test_null_metric_value_is_not_compared() -> None:
    """指标值为 "null"（不可计算）时不参与比较，不发比较结论。"""
    result = benchmark_compare.calculate(
        _request(industry="综合零售", benchmarks=_benchmarks()),
        EngineDataset(),
        kpi_metrics=[_metric("KPI.TURNOVER", "null")],
    )

    assert result.comparisons == ()
    assert [w.code for w in result.warnings] == ["BENCHMARK_UNAVAILABLE"]


def test_metric_contract_and_empty_inputs() -> None:
    """指标 name/unit/formula_version 冻结；无 KPI 指标时输出 null 不发比较结论。"""
    result = benchmark_compare.calculate(
        _request(industry="综合零售", benchmarks=_benchmarks()),
        EngineDataset(),
    )

    assert [m.formula_id for m in result.metrics] == list(benchmark_compare.FORMULA_IDS)
    assert result.metrics[0].name == "BM.DEVIATION_RATIO"
    assert result.metrics[0].unit == "ratio"
    assert result.metrics[0].formula_version == "0.1.0"
    assert result.metrics[0].value == "null"
    assert result.comparisons == ()


def test_invalid_updated_at_record_discarded() -> None:
    """updated_at 无法解析的记录被丢弃（必备字段不得为非法值）。"""
    def _record(**overrides: object) -> dict[str, Any]:
        base: dict[str, Any] = {
            "source": "测试来源",
            "region": "全国",
            "industry": "综合零售",
            "sample_scope": "测试样本",
            "updated_at": "2026-01-01",
            "benchmark_version": "1.0.0",
            "unit": "次/期间",
            "applicability": "仅测试",
            "metric": "KPI.TURNOVER",
            "value": "4.00",
        }
        base.update(overrides)
        return base

    result = benchmark_compare.calculate(
        _request(
            industry="综合零售",
            benchmarks=[_record(updated_at="not-a-date"), _record(updated_at="2026-05-05")],
        ),
        EngineDataset(),
        kpi_metrics=[_metric("KPI.TURNOVER", "6.75")],
    )

    assert result.discarded_records == 1
    assert result.comparisons[0].benchmark.updated_at == date(2026, 5, 5)


def test_version_comparison_prefers_higher_numeric_component() -> None:
    """版本号按数字分量比较（1.10.0 > 1.9.0），不受字典序影响。"""
    def _record(version: str, value: str) -> dict[str, Any]:
        return {
            "source": "测试来源",
            "region": "全国",
            "industry": "综合零售",
            "sample_scope": "测试样本",
            "updated_at": "2026-01-01",
            "benchmark_version": version,
            "unit": "次/期间",
            "applicability": "仅测试",
            "metric": "KPI.TURNOVER",
            "value": value,
        }

    result = benchmark_compare.calculate(
        _request(
            industry="综合零售",
            benchmarks=[_record("1.9.0", "3.00"), _record("1.10.0", "9.00")],
        ),
        EngineDataset(),
        kpi_metrics=[_metric("KPI.TURNOVER", "6.75")],
    )

    assert result.comparisons[0].benchmark.benchmark_version == "1.10.0"
    assert result.comparisons[0].benchmark_value == Decimal("9.00")
