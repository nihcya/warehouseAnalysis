"""实验模型注册与运行入口（非默认路径，显式调用才会进入）。

模型注册表是**惰性**的：``holt_winters`` 等依赖 statsmodels 的模型在被调用时才
尝试导入依赖，缺失时抛 :class:`ExperimentalModelError` 并给出安装提示，默认
分析路径完全不受影响。

M2 只交付隔离骨架：``seasonal_naive`` 为可运行的示例实现（纯 Decimal，无外部
依赖），``holt_winters`` 为占位桩（明确报"尚未实装"）。两者均**不进默认
AnalysisResult**，也不参与 formula-spec §10 的黄金数据验收。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal

from warehouse_engine.calculators.forecasting import (
    MOVING_AVERAGE_WEEKS,
    weekly_series,
)
from warehouse_engine.contracts import AnalysisRequest, EngineDataset
from warehouse_engine.experimental.types import (
    EXPERIMENTAL_DISCLAIMER,
    ExperimentalForecastResult,
)
from warehouse_engine.replay import replay_movements

#: 实验模型函数签名：(request, dataset, sku_id, horizon) → ExperimentalForecastResult
ModelFn = Callable[[AnalysisRequest, EngineDataset, str, int], ExperimentalForecastResult]

#: 季节性回退的历史周数（52 周 ≈ 一年）
SEASONAL_LAG_WEEKS = 52

_REGISTRY: dict[str, ModelFn] = {}


class ExperimentalModelError(RuntimeError):
    """实验模型不可用：未注册，或缺少可选依赖。"""


def register(name: str, fn: ModelFn) -> None:
    """注册一个实验模型（同名覆盖）。"""
    _REGISTRY[name] = fn


def available_models() -> tuple[str, ...]:
    """返回已注册的实验模型名（字典序，确定性）。"""
    return tuple(sorted(_REGISTRY))


def load_model(name: str) -> ModelFn:
    """取已注册的实验模型；未注册时抛 :class:`ExperimentalModelError`。"""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ExperimentalModelError(
            f"实验模型 {name!r} 未注册；可用模型：{', '.join(available_models()) or '（无）'}。"
        ) from None


def _seasonal_naive(
    request: AnalysisRequest,
    dataset: EngineDataset,
    sku_id: str,
    horizon: int,
) -> ExperimentalForecastResult:
    """季节性朴素（§8.1 候选模型，口径未冻结）：取上一年度同周需求。

    不足 52 周历史时按可用历史回退（取最近一个同模 52 的周），仍不足则回退
    最近一期需求。仅作实验参考，不参与验收。
    """
    outcome = replay_movements(request, dataset.movements)
    series = weekly_series(outcome.daily_out_by_sku.get(sku_id, ()))
    demands = tuple(week.demand for week in series)
    forecast: list[Decimal] = []
    for step in range(max(0, horizon)):
        index = len(demands) - SEASONAL_LAG_WEEKS + step
        if 0 <= index < len(demands):
            forecast.append(demands[index])
        elif demands:
            forecast.append(demands[-1])  # 历史不足一年：回退最近一期需求
        else:
            forecast.append(Decimal(0))
    return ExperimentalForecastResult(
        model="seasonal_naive",
        sku_id=sku_id,
        horizon=max(0, horizon),
        forecast=tuple(forecast),
        notes=(
            EXPERIMENTAL_DISCLAIMER,
            "季节性朴素（上一年度同周需求）；不足 52 周历史时按可用历史回退。",
            f"口径未冻结，默认路径仍为 {MOVING_AVERAGE_WEEKS} 周移动平均基线（F-FCST-001）。",
        ),
    )


def _holt_winters_stub(
    request: AnalysisRequest,
    dataset: EngineDataset,
    sku_id: str,
    horizon: int,
) -> ExperimentalForecastResult:
    """Holt-Winters 占位桩：M2 仅交付隔离骨架，尚未实装模型本体。

    ``statsmodels`` 已在主依赖中声明（M0 起），故本桩不因缺依赖而报错，而是
    显式报"尚未实装"——避免把未冻结口径当作可用结果使用。依赖层隔离留待
    M3 真正引入 statsmodels 模型时再收紧为可选依赖组。
    """
    del request, dataset, sku_id, horizon
    raise ExperimentalModelError(
        "Holt-Winters 为 M2 实验内容，本版本仅交付隔离骨架，模型本体尚未实装。"
        "实现方式：在本模块注册一个 ModelFn 实现后，显式调用 "
        "run_experimental(model='holt_winters')。口径经 docs/formula-spec.md "
        "冻结前不得进入默认 AnalysisResult。"
    )


register("seasonal_naive", _seasonal_naive)
register("holt_winters", _holt_winters_stub)


def run_experimental(
    request: AnalysisRequest,
    dataset: EngineDataset,
    *,
    model: str = "seasonal_naive",
    sku_ids: Sequence[str] = (),
    horizon: int = 1,
) -> tuple[ExperimentalForecastResult, ...]:
    """显式运行实验模型，返回实验结果（**不进入默认 AnalysisResult**）。

    - ``model``：模型名，见 :func:`available_models`；
    - ``sku_ids``：指定 SKU；为空时对数据集中全部有需求序列的 SKU 运行；
    - ``horizon``：预测步长（周）。
    """
    fn = load_model(model)
    outcome = replay_movements(request, dataset.movements)
    targets = tuple(sku_ids) if sku_ids else tuple(sorted(outcome.daily_out_by_sku))
    return tuple(fn(request, dataset, sku_id, horizon) for sku_id in targets)
