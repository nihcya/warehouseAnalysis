"""forecasting 计算器测试（F-FCST-001~002，formula-spec §8）。

用例覆盖 formula-spec §8.4 声明的黄金数据三组：

- ≥12 期周需求（含季节性与零需求期）：13 周主算例，手工推导滚动预测与
  MAPE/WAPE/MAE/RMSE；
- n < 12：有效需求期数不足时强制降级并发 ``INSUFFICIENT_SAMPLES``；
- 切分边界：``split_date`` 显式传入与缺省（``end_date − 28 天``）的行为。

主算例（手工推导）：13 个自然周（2026-01-05 周一 ~ 2026-04-06），逐周需求
``10,20,30,40,0,20,30,40,10,20,30,0,10``；缺省 split_date = 2026-03-09 →
预测窗口为第 10~13 周。滚动一步向前（各取其前 4 周实际均值）：

- w10 预测 (20+30+40+10)/4 = 25，实际 20，误差 −5
- w11 预测 (30+40+10+20)/4 = 25，实际 30，误差 +5
- w12 预测 (40+10+20+30)/4 = 25，实际 0，误差 −25（d=0 不计入 MAPE）
- w13 预测 (10+20+30+0)/4 = 15，实际 10，误差 −5

→ WAPE = 40/60、MAE = 40/4 = 10、RMSE = √175、MAPE = 100×(0.25+1/6+0.5)/3
≈ 30.555556；下一周预测 = (20+30+0+10)/4 = 15，日均 = 15/7。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from contracts import (
    AnalysisRequest,
    EngineDataset,
    EventSource,
    MovementRecord,
    SkuRecord,
)
from warehouse_engine.calculators import forecasting

UNIT = "件"
START = date(2026, 1, 5)  # 周一
END = date(2026, 4, 6)  # 周一，13 个自然周
WEEKLY_DEMANDS = ["10", "20", "30", "40", "0", "20", "30", "40", "10", "20", "30", "0", "10"]


def _request(end: date = END, **parameters: object) -> AnalysisRequest:
    return AnalysisRequest(
        run_id="run-forecasting",
        start_date=START,
        end_date=end,
        warehouse_ids=["WH-01"],
        parameters=dict(parameters),
    )


def _movement(
    event_id: str,
    sku_id: str,
    move_type: str,
    move_date: date,
    quantity: str,
) -> MovementRecord:
    return MovementRecord(
        event_id=event_id,
        sku_id=sku_id,
        move_type=move_type,
        quantity=Decimal(quantity),
        move_date=move_date,
        occurred_at=datetime(move_date.year, move_date.month, move_date.day, 8, 0, tzinfo=UTC),
        warehouse_id="WH-01",
        source=EventSource.IMPORT,
    )


def _sku(sku_id: str = "SKU-0001") -> SkuRecord:
    return SkuRecord(sku_id=sku_id, name=sku_id, category="测试", unit=UNIT)


def _weekly_dataset(demands: list[str], *, end: date = END) -> EngineDataset:
    """按周一首日放置出库事件；另加一条入库确保 SKU 存在重放桶（全零需求时亦然）。"""
    movements = [_movement("IN-0", "SKU-0001", "INBOUND", date(2025, 12, 1), "10000")]
    for index, quantity in enumerate(demands):
        if Decimal(quantity) == 0:
            continue
        day = START + timedelta(weeks=index)
        movements.append(_movement(f"OUT-{index}", "SKU-0001", "OUTBOUND", day, quantity))
    return EngineDataset(skus=[_sku()], movements=movements)


def _close(actual: Decimal, expected: str, tolerance: str = "0.000001") -> bool:
    return abs(actual - Decimal(expected)) <= Decimal(tolerance)


# --- F-FCST-001 预测 --------------------------------------------------------


def test_weekly_aggregation_uses_monday_as_week_start() -> None:
    """需求按自然周聚合，周一为周首（§8.1）。"""
    dataset = _weekly_dataset(WEEKLY_DEMANDS)
    result = forecasting.calculate(_request(), dataset)

    weeks = result.per_sku[0].weekly_demand
    assert len(weeks) == 13
    assert weeks[0].week_start == START
    assert all(week.week_start.weekday() == 0 for week in weeks)
    assert [str(week.demand) for week in weeks] == WEEKLY_DEMANDS


def test_next_week_forecast_is_mean_of_last_four_weeks() -> None:
    """下一周预测 = 最近 4 周需求算术平均 = (20+30+0+10)/4 = 15；日均 = 15/7。"""
    dataset = _weekly_dataset(WEEKLY_DEMANDS)
    result = forecasting.calculate(_request(), dataset)

    row = result.per_sku[0]
    assert row.next_week_forecast == Decimal(15)
    assert _close(row.daily_forecast, "2.142857")


def test_default_split_date_is_end_minus_28_days() -> None:
    """split_date 缺省为 end_date − 28 天（§8.1）。"""
    dataset = _weekly_dataset(WEEKLY_DEMANDS)
    result = forecasting.calculate(_request(), dataset)

    assert result.split_date == END - timedelta(days=28)
    assert result.per_sku[0].train_periods == 9
    assert result.per_sku[0].forecast_periods == 4


def test_explicit_split_date_is_honoured() -> None:
    """显式传入 split_date 时按其切分训练/预测窗口（按时间顺序，不打乱）。"""
    dataset = _weekly_dataset(WEEKLY_DEMANDS)
    result = forecasting.calculate(_request(split_date="2026-02-02"), dataset)

    assert result.split_date == date(2026, 2, 2)
    assert result.per_sku[0].train_periods == 4
    assert result.per_sku[0].forecast_periods == 9


def test_fewer_than_four_weeks_averages_over_available_weeks() -> None:
    """可用历史不足 4 周时按实际周数取均值（不补零、不外推）。"""
    # 仅 2 周数据：下一周预测 = (10 + 30) / 2 = 20
    dataset = _weekly_dataset(["10", "30"])
    result = forecasting.calculate(_request(end=date(2026, 1, 19)), dataset)

    row = result.per_sku[0]
    assert len(row.weekly_demand) == 2
    assert row.next_week_forecast == Decimal(20)


def test_split_at_end_yields_empty_forecast_window() -> None:
    """split_date = end_date 时预测窗口为空 → MAPE null（empty_forecast_window）。"""
    dataset = _weekly_dataset(WEEKLY_DEMANDS)
    result = forecasting.calculate(_request(split_date="2026-04-06"), dataset)

    mape = next(m for m in result.metrics if m.formula_id == "F-FCST-002")
    assert mape.value == "null"
    assert mape.reason == "empty_forecast_window"


# --- F-FCST-002 误差 --------------------------------------------------------


def test_error_metrics_match_hand_calculation() -> None:
    """WAPE = 40/60、MAE = 10、RMSE = √175、MAPE ≈ 30.555556（见模块文档算例）。"""
    dataset = _weekly_dataset(WEEKLY_DEMANDS)
    result = forecasting.calculate(_request(), dataset)
    row = result.per_sku[0]

    assert _close(row.wape, "0.666667")
    assert row.mae == Decimal(10)
    assert _close(row.rmse, "13.228757")
    assert _close(row.mape, "30.555556")


def test_aggregated_mape_excludes_zero_demand_periods() -> None:
    """d_t = 0 的期不计入 MAPE（n′ 只统计 d_t > 0 的期）。"""
    dataset = _weekly_dataset(WEEKLY_DEMANDS)
    result = forecasting.calculate(_request(), dataset)

    mape = next(m for m in result.metrics if m.formula_id == "F-FCST-002")
    assert mape.sample_count == 3  # w10 / w11 / w13，w12 需求为 0 被排除
    assert _close(Decimal(str(mape.value)), "30.555556")


def test_zero_demand_window_yields_null_mape() -> None:
    """预测窗口实际需求全为 0 → n′ = 0，MAPE 输出 null（zero_actual_demand）。"""
    demands = ["10", "20", "30", "40", "10", "20", "30", "40", "10", "0", "0", "0", "0"]
    dataset = _weekly_dataset(demands)
    result = forecasting.calculate(_request(), dataset)

    mape = next(m for m in result.metrics if m.formula_id == "F-FCST-002")
    assert mape.value == "null"
    assert mape.reason == "zero_actual_demand"
    # WAPE 同样因 Σd = 0 输出 null（§8.2）
    assert result.per_sku[0].wape is None


# --- 样本不足降级 -----------------------------------------------------------


def test_insufficient_samples_warns_and_downgrades() -> None:
    """有效需求期数 n < 12 → 强制降级基线 + INSUFFICIENT_SAMPLES(fields: sku, n)。"""
    dataset = _weekly_dataset(["10", "20", "30", "40", "10", "20", "30", "40"])
    result = forecasting.calculate(_request(end=date(2026, 3, 2)), dataset)

    row = result.per_sku[0]
    assert len(row.weekly_demand) == 8
    assert row.downgraded is True
    assert row.downgrade_reason == "insufficient_samples"
    assert [(w.code, w.fields) for w in result.warnings] == [
        ("INSUFFICIENT_SAMPLES", ["SKU-0001", "8"])
    ]
    # 降级后误差指标照常输出，仅标注"不参与达标判定"
    assert row.mae is not None


def test_sufficient_samples_emits_no_warning() -> None:
    """有效需求期数 ≥ 12 时不发 INSUFFICIENT_SAMPLES。"""
    dataset = _weekly_dataset(WEEKLY_DEMANDS)
    result = forecasting.calculate(_request(), dataset)

    assert result.warnings == ()
    assert result.per_sku[0].downgraded is False


# --- 指标契约与边界 ---------------------------------------------------------


def test_metric_ids_names_and_units() -> None:
    """两个指标携带冻结的 formula_id / formula_version / name / unit。"""
    dataset = _weekly_dataset(WEEKLY_DEMANDS)
    result = forecasting.calculate(_request(), dataset)

    assert [m.formula_id for m in result.metrics] == list(forecasting.FORMULA_IDS)
    assert [m.name for m in result.metrics] == ["FCST.NEXT_WEEK_DEMAND", "FCST.MAPE"]
    assert [m.unit for m in result.metrics] == ["件", "ratio"]
    assert all(m.formula_version == "0.1.0" for m in result.metrics)


def test_next_week_demand_metric_is_sku_sum() -> None:
    """FCST.NEXT_WEEK_DEMAND 为各 SKU 下一周预测之和，sample_count 为 SKU 数。"""
    dataset = _weekly_dataset(WEEKLY_DEMANDS)
    result = forecasting.calculate(_request(), dataset)

    metric = next(m for m in result.metrics if m.formula_id == "F-FCST-001")
    assert Decimal(str(metric.value)) == Decimal(15)
    assert metric.sample_count == 1


def test_empty_dataset_returns_null_metrics() -> None:
    """空数据集：两项指标输出 "null"，不发 Warning。"""
    result = forecasting.calculate(_request(), EngineDataset())

    assert [m.value for m in result.metrics] == ["null", "null"]
    assert next(m for m in result.metrics if m.formula_id == "F-FCST-001").reason == (
        "empty_dataset"
    )
    assert result.warnings == ()
    assert result.per_sku == ()


def test_week_start_helper_and_split_date_parsing() -> None:
    """周首为周一；split_date 支持 date 与 ISO 字符串两种形态。"""
    assert forecasting.week_start_of(date(2026, 6, 3)) == date(2026, 6, 1)  # 周三 → 周一
    assert forecasting.week_start_of(date(2026, 6, 1)) == date(2026, 6, 1)  # 周一 → 自身

    assert forecasting.resolve_split_date(_request()) == END - timedelta(days=28)
    assert forecasting.resolve_split_date(_request(split_date="2026-03-01")) == date(2026, 3, 1)
    assert forecasting.resolve_split_date(_request(split_date=date(2026, 3, 1))) == date(
        2026, 3, 1
    )
