"""行业基准比较计算器（M0 存根）。

公式口径冻结于 ``docs/formula-spec.md``，公式 ID 见 :data:`FORMULA_IDS`：
基准必须携带来源、地区、行业、样本范围与版本；无匹配基准时返回
BENCHMARK_UNAVAILABLE，不允许使用无来源的经验数字作为默认基准。
"""

from __future__ import annotations

from typing import Any, NoReturn

#: 本模块负责的公式 ID（基准比较口径，编号冻结于 docs/formula-spec.md）
FORMULA_IDS: tuple[str, ...] = ("F-BM-001",)  # 行业基准比较 benchmark_compare


def calculate(*args: Any, **kwargs: Any) -> NoReturn:
    """M0 存根：基准比较将在 M2 实现。"""
    raise NotImplementedError("benchmark_compare.calculate 尚未实现（M0 仅冻结契约与公式 ID）")
