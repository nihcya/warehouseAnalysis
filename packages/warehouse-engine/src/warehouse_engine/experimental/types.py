"""实验模型结果类型：与契约 ResultMetric 完全解耦的独立类型。

:class:`ExperimentalForecastResult` **不继承** :class:`contracts.analysis.ResultMetric`，
也不提供任何到它的转换方法，从而在类型层面阻断实验输出进入默认
``AnalysisResult``（formula-spec §8.1）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: 实验输出的固定免责声明（随每条结果返回，提示口径未冻结）
EXPERIMENTAL_DISCLAIMER = (
    "实验模型输出：口径未冻结，不进入默认 AnalysisResult，"
    "不参与黄金数据验收，不得作为采购依据。"
)


@dataclass(frozen=True)
class ExperimentalForecastResult:
    """单个 SKU 的实验模型预测结果（非默认口径）。

    与 :class:`ResultMetric` 无继承或转换关系，``build_analysis_result`` 的
    ``metrics`` 参数（``Sequence[ResultMetric]``）在类型上即无法接收本类型。
    """

    #: 模型名（见 experimental.forecast_models.available_models）
    model: str
    sku_id: str
    #: 预测步长（周）
    horizon: int
    #: 预测序列（按步升序）
    forecast: tuple[Decimal, ...]
    #: 口径说明与限制（含 EXPERIMENTAL_DISCLAIMER）
    notes: tuple[str, ...]
