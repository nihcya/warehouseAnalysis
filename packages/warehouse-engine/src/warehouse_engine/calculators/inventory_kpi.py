"""库存 KPI 计算器（M0 存根）。

公式口径冻结于 ``docs/formula-spec.md``，公式 ID 见 :data:`FORMULA_IDS`：
期初/期末库存、COGS、退货、盘点与负库存处理口径以该文档为准。
"""

from __future__ import annotations

from typing import Any, NoReturn

#: 本模块负责的公式 ID（KPI/COGS 口径，编号冻结于 docs/formula-spec.md）
FORMULA_IDS: tuple[str, ...] = (
    "F-KPI-001",  # 期初库存 opening_qty
    "F-KPI-002",  # 期末库存 closing_qty
    "F-KPI-003",  # 出库量 out_qty
    "F-KPI-004",  # 动销率 active_ratio
    "F-KPI-005",  # 库存价值 inventory_value
    "F-KPI-006",  # 周转率 turnover
    "F-KPI-007",  # 周转天数 turnover_days
    "F-KPI-008",  # 覆盖天数 coverage_days
    "F-COGS-001",  # 销售成本 COGS（移动加权平均）
)


def calculate(*args: Any, **kwargs: Any) -> NoReturn:
    """M0 存根：KPI 计算将在 M1 实现。"""
    raise NotImplementedError("inventory_kpi.calculate 尚未实现（M0 仅冻结契约与公式 ID）")
