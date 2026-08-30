"""需求预测与误差评估计算器（F-FCST-001~002，formula-spec §8）。

实现要点：

- **默认模型（§8.1）**：M0 冻结为 **4 周移动平均基线**（季节性朴素列为后续
  候选）。需求按自然周聚合（**周一为周首**），下一周预测为最近 4 周需求的
  算术平均 ``ŵ_{K+1} = (w_K + w_{K−1} + w_{K−2} + w_{K−3}) / 4``；日均预测 =
  周预测 / 7。首尾不完整的自然周照常参与聚合，不丢弃；
- **窗口切分（§8.1）**：严格按时间顺序切分，**禁止随机打乱**。训练窗口
  ``[start_date, split_date)``、预测窗口 ``[split_date, end_date)``；
  ``split_date`` 由 ``parameters["split_date"]`` 传入，缺省 ``end_date − 28 天``；
- **误差指标（§8.2）**：在预测窗口上按滚动一步向前（rolling one-step-ahead）
  计算——每个预测周的预测值取其前 4 个实际周需求的均值（可用不足 4 周时按
  实际周数 k），与实际需求比对。``MAPE = (100/n′) × Σ_{d_t>0} |e_t|/d_t``
  （``d_t = 0`` 的期不计入，``n′ = 0`` 时输出 null）、``WAPE = Σ|e_t|/Σd_t``
  （``Σd_t = 0`` 时 null）、``MAE = Σ|e_t|/n``、``RMSE = √(Σe_t²/n)``，其中
  ``n`` 为预测窗口期数；``RMSE`` 用 ``Decimal.sqrt()``；
- **样本不足（§8.3）**：判定用的 **n 为全期间有效需求期数**（见下方口径
  澄清），``n < 12`` 时强制降级为基线模型并发 ``INSUFFICIENT_SAMPLES``
  （fields: sku_id、n），误差指标照常输出并标注"样本不足，不参与达标判定"；
- **口径澄清（M2 实现决策，需 A/B 复核）**：§8.2 将 ``n`` 明确定义为"预测
  窗口期数"并用于 MAE/RMSE 分母；而 §8.3 的"有效需求期数 n"若同样按预测窗口
  理解，则在默认 ``split_date = end_date − 28 天`` 下 n ≈ 4 会恒定触发降级，
  与 §8.4 黄金数据"≥12 期周需求"的要求矛盾。故本模块取：误差指标的分母
  ``n`` = 预测窗口期数（§8.2 原文），样本不足判定的 ``n`` = 分析期间内的自然
  周数即有效需求期数（§8.3 + §8.4 的 ≥12 期口径）。两者分别命名为
  ``forecast_periods`` 与 ``demand_periods``，Warning fields 取后者；
- **聚合口径**：``FCST.NEXT_WEEK_DEMAND`` 为各 SKU 下一周预测之和；
  ``FCST.MAPE`` 按**合并口径**计算（把所有 SKU × 预测期的误差 pooled 后套用
  §8.2 公式），避免对不可计算的 SKU 取均值产生偏倚；WAPE/MAE/RMSE 同样按合并
  口径留在返回对象内（一个 formula_id 只承载一个指标）；
- **实验模型**：statsmodels 等模型属 ``experimental/``，不进默认
  ``AnalysisResult``，本模块不引用；
- **确定性**：周首由 ``date.weekday()`` 推出，SKU 按字典序、周按日期升序，
  全链路 Decimal（无 float、无 ``now()``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from warehouse_engine.calculators._common import (
    NULL_VALUE,
    build_metric,
    round_ratio,
)
from warehouse_engine.contracts import (
    AnalysisRequest,
    EngineDataset,
    ResultMetric,
    Warning,
    WarningSeverity,
)
from warehouse_engine.replay import ReplayOutcome, replay_movements

#: 本模块负责的公式 ID（预测与误差口径，编号冻结于 docs/formula-spec.md）
FORMULA_IDS: tuple[str, ...] = (
    "F-FCST-001",  # 默认预测模型（4 周移动平均基线）
    "F-FCST-002",  # 误差指标（MAPE/WAPE/MAE/RMSE）
)

#: 输出指标名（AnalysisResult.metrics[].name 的稳定标识）
METRIC_NAMES: dict[str, str] = {
    "F-FCST-001": "FCST.NEXT_WEEK_DEMAND",
    "F-FCST-002": "FCST.MAPE",
}

#: 移动平均窗口（周，§8.1 冻结为 4 周）
MOVING_AVERAGE_WEEKS = 4

#: split_date 缺省回退：end_date − 28 天（§8.1）
DEFAULT_SPLIT_OFFSET_DAYS = 28

#: 样本不足降级阈值：有效需求期数 n < 12（§8.3）
MIN_SAMPLES = 12


@dataclass(frozen=True)
class WeeklyDemand:
    """单个自然周的聚合需求（周一为周首）。"""

    week_start: date
    demand: Decimal


@dataclass(frozen=True)
class SkuForecast:
    """单个 SKU 的预测与误差明细。"""

    sku_id: str
    #: 全期间逐周需求（按周首升序，含零需求周）
    weekly_demand: tuple[WeeklyDemand, ...]
    #: 训练窗口周数
    train_periods: int
    #: 预测窗口周数（§8.2 的 n）
    forecast_periods: int
    #: 下一周（end_date 之后一周）需求预测；训练数据不足时为 None
    next_week_forecast: Decimal | None
    #: 日均预测 = 下一周预测 / 7
    daily_forecast: Decimal | None
    mape: Decimal | None
    wape: Decimal | None
    mae: Decimal | None
    rmse: Decimal | None
    #: 是否降级（样本不足强制走基线模型）
    downgraded: bool
    #: 降级原因：None / "insufficient_samples" / "insufficient_training_weeks"
    downgrade_reason: str | None


@dataclass(frozen=True)
class ForecastingResult:
    """预测与误差评估输出：聚合指标、Warning 与 SKU 级明细。"""

    metrics: tuple[ResultMetric, ...]
    warnings: tuple[Warning, ...]
    per_sku: tuple[SkuForecast, ...]
    #: 实际使用的训练/预测窗口切分日期
    split_date: date


def week_start_of(day: date) -> date:
    """自然周周首（周一为周首，§8.1）。"""
    return day - timedelta(days=day.weekday())


def resolve_split_date(request: AnalysisRequest) -> date:
    """确定切分日期：``parameters["split_date"]`` 优先，缺省 ``end_date − 28 天``。"""
    raw = request.parameters.get("split_date")
    if raw is None:
        return request.end_date - timedelta(days=DEFAULT_SPLIT_OFFSET_DAYS)
    if isinstance(raw, datetime):  # datetime 是 date 的子类，需先判定
        return raw.date()
    if isinstance(raw, date):
        return raw
    return date.fromisoformat(str(raw))


def weekly_series(daily: tuple[tuple[date, Decimal], ...]) -> tuple[WeeklyDemand, ...]:
    """把逐日需求聚合为自然周序列（按周首升序，含零需求周）。"""
    buckets: dict[date, Decimal] = {}
    for day, demand in daily:
        key = week_start_of(day)
        buckets[key] = buckets.get(key, Decimal(0)) + demand
    return tuple(
        WeeklyDemand(week_start=key, demand=buckets[key]) for key in sorted(buckets)
    )


def _moving_average(values: tuple[Decimal, ...], count: int) -> Decimal | None:
    """取末尾 ``count`` 项的算术平均；不足 ``count`` 项时按实际项数，0 项为 None。"""
    window = values[-count:] if count > 0 else ()
    if not window:
        return None
    return sum(window, Decimal(0)) / Decimal(len(window))


def _forecast_for_sku(
    sku_id: str,
    weekly: tuple[WeeklyDemand, ...],
    split_date: date,
) -> tuple[SkuForecast, bool]:
    """计算单个 SKU 的预测与误差，返回 (明细, 是否样本不足)。"""
    demands = tuple(week.demand for week in weekly)
    forecast_weeks = [week for week in weekly if week.week_start >= split_date]
    train_periods = len(weekly) - len(forecast_weeks)
    forecast_periods = len(forecast_weeks)
    demand_periods = len(weekly)

    errors: list[Decimal] = []
    actuals: list[Decimal] = []
    for index, week in enumerate(weekly):
        if week.week_start < split_date:
            continue
        predicted = _moving_average(demands[:index], MOVING_AVERAGE_WEEKS)
        if predicted is None:
            continue
        errors.append(week.demand - predicted)
        actuals.append(week.demand)

    next_week = _moving_average(demands, MOVING_AVERAGE_WEEKS)
    downgrade_reason: str | None = None
    insufficient_samples = demand_periods < MIN_SAMPLES
    if insufficient_samples:
        downgrade_reason = "insufficient_samples"
    if next_week is None and downgrade_reason is None:
        downgrade_reason = "insufficient_training_weeks"

    mape: Decimal | None = None
    wape: Decimal | None = None
    mae: Decimal | None = None
    rmse: Decimal | None = None
    if errors:
        count = Decimal(len(errors))
        total_actual = sum(actuals, Decimal(0))
        absolute = [abs(error) for error in errors]
        scored = [
            (abs(error), actual)
            for error, actual in zip(errors, actuals, strict=True)
            if actual > 0
        ]
        n_prime = Decimal(len(scored))
        if n_prime > 0:
            mape = sum((abs_err / actual for abs_err, actual in scored), Decimal(0)) / n_prime
            mape = mape * Decimal(100)
        if total_actual > 0:
            wape = sum(absolute, Decimal(0)) / total_actual
        mae = sum(absolute, Decimal(0)) / count
        rmse = (sum((error**2 for error in errors), Decimal(0)) / count).sqrt()

    return (
        SkuForecast(
            sku_id=sku_id,
            weekly_demand=weekly,
            train_periods=train_periods,
            forecast_periods=forecast_periods,
            next_week_forecast=next_week,
            daily_forecast=None if next_week is None else next_week / Decimal(7),
            mape=mape,
            wape=wape,
            mae=mae,
            rmse=rmse,
            downgraded=downgrade_reason is not None,
            downgrade_reason=downgrade_reason,
        )
    ), insufficient_samples


def calculate(
    request: AnalysisRequest,
    dataset: EngineDataset,
    *,
    outcome: ReplayOutcome | None = None,
) -> ForecastingResult:
    """按 formula-spec §8 计算 F-FCST-001（4 周移动平均基线）与 F-FCST-002（误差）。"""
    if outcome is None:
        outcome = replay_movements(request, dataset.movements)

    split_date = resolve_split_date(request)
    rows: list[SkuForecast] = []
    warnings: list[Warning] = []
    next_week_total = Decimal(0)
    next_week_count = 0
    mape_numerator = Decimal(0)
    scored_pairs = 0
    pooled_periods = 0

    for sku_id in sorted(outcome.daily_out_by_sku):
        weekly = weekly_series(outcome.daily_out_by_sku[sku_id])
        if not weekly:
            continue
        row, insufficient = _forecast_for_sku(sku_id, weekly, split_date)
        rows.append(row)
        if insufficient:
            warnings.append(
                Warning(
                    code="INSUFFICIENT_SAMPLES",
                    severity=WarningSeverity.WARN,
                    message=(
                        f"SKU {sku_id} 有效需求期数 {len(weekly)} < {MIN_SAMPLES}，"
                        "强制降级为 4 周移动平均基线模型；误差指标照常输出但"
                        "标注样本不足，不参与达标判定。"
                    ),
                    fields=[sku_id, str(len(weekly))],
                    blocking=False,
                )
            )
        if row.next_week_forecast is not None:
            next_week_total += row.next_week_forecast
            next_week_count += 1

    # 合并口径（pooled）重算聚合 MAPE：把所有 (SKU × 预测期) 的误差 pooled 后
    # 套用 §8.2 公式，避免对不可计算的 SKU 取均值产生偏倚
    for row in rows:
        for index, week in enumerate(row.weekly_demand):
            if week.week_start < split_date:
                continue
            predicted = _moving_average(
                tuple(item.demand for item in row.weekly_demand[:index]),
                MOVING_AVERAGE_WEEKS,
            )
            if predicted is None:
                continue
            error = week.demand - predicted
            pooled_periods += 1
            if week.demand > 0:
                scored_pairs += 1
                mape_numerator += abs(error) / week.demand

    if next_week_count == 0:
        next_week_metric = build_metric(
            "F-FCST-001",
            METRIC_NAMES["F-FCST-001"],
            NULL_VALUE,
            "件",
            0,
            reason="insufficient_training_weeks" if rows else "empty_dataset",
        )
    else:
        next_week_metric = build_metric(
            "F-FCST-001",
            METRIC_NAMES["F-FCST-001"],
            str(next_week_total),
            "件",
            next_week_count,
        )

    if scored_pairs == 0:
        mape_metric = build_metric(
            "F-FCST-002",
            METRIC_NAMES["F-FCST-002"],
            NULL_VALUE,
            "ratio",
            0,
            reason="empty_forecast_window" if pooled_periods == 0 else "zero_actual_demand",
        )
    else:
        mape_value = mape_numerator / Decimal(scored_pairs) * Decimal(100)
        mape_metric = build_metric(
            "F-FCST-002",
            METRIC_NAMES["F-FCST-002"],
            round_ratio(mape_value),
            "ratio",
            scored_pairs,
        )

    return ForecastingResult(
        metrics=(next_week_metric, mape_metric),
        warnings=tuple(warnings),
        per_sku=tuple(rows),
        split_date=split_date,
    )
