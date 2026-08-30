"""实验模型隔离区（**非默认口径，输出不进入 AnalysisResult**）。

本包是 `docs/formula-spec.md` §8.1 所要求的"实验模型与默认模型隔离"落点：

> statsmodels 模型（Holt-Winters、ARIMA 等）为 M2 实验内容，位于
> ``experimental/``，不进默认 ``AnalysisResult``。

三重隔离保证：

1. **类型层**：:class:`ExperimentalForecastResult` 是独立类型，既不继承也不
   提供到 :class:`contracts.analysis.ResultMetric` 的转换，
   ``build_analysis_result(metrics=...)`` 无法接收它；
2. **导入层**：``engine.py`` 与四个计算器（abc_aging / replenishment /
   forecasting / benchmark_compare）**均不 import 本包**；只有显式调用
   :func:`run_experimental` 才会进入实验路径，statsmodels 亦为惰性导入；
3. **依赖层**：默认路径不 import ``statsmodels``（由
   ``tests/engine/test_experimental_isolation.py`` 用子进程断言）。
   ``statsmodels`` 目前仍在 pyproject 主依赖中（M0 起即如此，M2 不改动依赖
   声明以避免 uv.lock 无谓 churn）；M3 真正引入 statsmodels 模型时再将其
   收紧为 ``[project.optional-dependencies] experimental``。

默认预测路径始终是 ``calculators/forecasting.py`` 的 4 周移动平均基线
（F-FCST-001，formula_version 0.1.0 已冻结）。本包内的模型口径**未冻结**，
结果不得作为采购依据，也不参与 §10 的黄金数据验收。
"""

from warehouse_engine.experimental.types import (
    EXPERIMENTAL_DISCLAIMER,
    ExperimentalForecastResult,
)

__all__ = [
    "EXPERIMENTAL_DISCLAIMER",
    "ExperimentalForecastResult",
]
