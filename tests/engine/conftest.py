"""tests/engine 共享测试辅助：formula-spec §10 容差比较。

黄金与边界 fixture 的指标层数值断言统一使用本模块的容差比较：
容差表冻结于 docs/formula-spec.md §10，按指标类别声明绝对/相对容差，
满足其一即通过；null 值（契约中以字符串 "null" 表示）按等价判定。
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest

#: §10 容差表：指标类别 → (绝对容差, 相对容差)
TOLERANCE_TABLE: dict[str, tuple[str, str]] = {
    # KPI 金额类（库存价值、COGS、呆滞金额、出库金额）
    "money": ("0.01", "1e-6"),
    # KPI 数量类（期初/期末/出库量、SS/ROP/Q*）
    "qty": ("0.001", "1e-9"),
    # KPI 比率/天数类（动销率、周转率、周转天数、覆盖天数）
    "ratio": ("1e-6", "1e-6"),
}


def _is_null(value: str | None) -> bool:
    """契约 null 表示：None 或字符串 "null"。"""
    return value is None or value == "null"


def assert_value_within_tolerance(
    expected: str | None,
    actual: str | None,
    kind: str,
) -> None:
    """按 §10 容差比较两个指标值（kind 见 TOLERANCE_TABLE）。"""
    if _is_null(expected):
        assert _is_null(actual), f"期望 null（{kind}），实际 {actual!r}"
        return
    assert not _is_null(actual), f"期望 {expected}（{kind}），实际 null"
    expected_value = Decimal(str(expected))
    actual_value = Decimal(str(actual))
    abs_tol, rel_tol = (Decimal(value) for value in TOLERANCE_TABLE[kind])
    difference = abs(actual_value - expected_value)
    within_abs = difference <= abs_tol
    within_rel = difference <= rel_tol * abs(expected_value)
    assert within_abs or within_rel, (
        f"期望 {expected}，实际 {actual}：超出 {kind} 容差"
        f"（abs 差 {difference} > {abs_tol}，且相对差超过 {rel_tol}）"
    )


@pytest.fixture
def tolerance_check() -> Callable[[str | None, str | None, str], None]:
    """返回 §10 容差比较函数：(expected, actual, kind)。"""
    return assert_value_within_tolerance
