"""ABC 分层、库龄与呆滞计算器（M0 存根）。

公式口径冻结于 ``docs/formula-spec.md``，公式 ID 见 :data:`FORMULA_IDS`：
排序指标、累计占比、并列值归属、左闭右开区间与呆滞观察窗口均以该文档为准。
"""

from __future__ import annotations

from typing import Any, NoReturn

#: 本模块负责的公式 ID（ABC/库龄/呆滞口径，编号冻结于 docs/formula-spec.md）
FORMULA_IDS: tuple[str, ...] = (
    "F-ABC-001",  # 期间 ABC 分类 classification
    "F-AGE-001",  # 期末库龄分布 inventory_age
    "F-STALE-001",  # 呆滞判定 stale_identification
)


def calculate(*args: Any, **kwargs: Any) -> NoReturn:
    """M0 存根：ABC/库龄/呆滞计算将在 M2 实现。"""
    raise NotImplementedError("abc_aging.calculate 尚未实现（M0 仅冻结契约与公式 ID）")
