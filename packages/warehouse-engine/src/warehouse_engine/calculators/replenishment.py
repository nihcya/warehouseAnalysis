"""补货计算器（M0 存根）。

公式口径冻结于 ``docs/formula-spec.md``，公式 ID 见 :data:`FORMULA_IDS`：
安全库存（SS = Z·σ·√L）、补货点（ROP = 平均日需求×L + SS）与经济订购量（EOQ）。
"""

from __future__ import annotations

from typing import Any, NoReturn

#: 本模块负责的公式 ID（补货口径，编号冻结于 docs/formula-spec.md）
FORMULA_IDS: tuple[str, ...] = (
    "F-REPL-001",  # 安全库存 safety_stock（SS = z·σ_d·√LT）
    "F-REPL-002",  # 补货点 reorder_point（ROP = d̄·LT + SS）
    "F-REPL-003",  # 建议补货量 suggested_qty（Q* = max(0, ROP − 库存 − 在途)）
)


def calculate(*args: Any, **kwargs: Any) -> NoReturn:
    """M0 存根：补货计算将在 M2 实现。"""
    raise NotImplementedError("replenishment.calculate 尚未实现（M0 仅冻结契约与公式 ID）")
