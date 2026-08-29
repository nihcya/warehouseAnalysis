"""需求预测与误差评估计算器（M0 存根）。

公式口径冻结于 ``docs/formula-spec.md``，公式 ID 见 :data:`FORMULA_IDS`：
移动平均基线模型、MAPE/MAE/RMSE 误差口径、训练/预测窗口与样本不足降级规则。
"""

from __future__ import annotations

from typing import Any, NoReturn

#: 本模块负责的公式 ID（预测与误差口径，编号冻结于 docs/formula-spec.md）
FORMULA_IDS: tuple[str, ...] = (
    "F-FCST-001",  # 默认预测模型（4 周移动平均基线）
    "F-FCST-002",  # 误差指标（MAPE/WAPE/MAE/RMSE）
)


def calculate(*args: Any, **kwargs: Any) -> NoReturn:
    """M0 存根：预测与误差评估将在 M2 实现。"""
    raise NotImplementedError("forecasting.calculate 尚未实现（M0 仅冻结契约与公式 ID）")
