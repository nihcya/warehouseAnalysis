"""M2 计算器共享辅助：舍入口径、null 表示、参数读取与指标构造。

formula-spec §2.2 冻结的舍入与 null 语义在五个计算器中完全一致，本模块给出
唯一实现，避免新增计算器各写一份而漂移：

- 中间计算不舍入，仅在指标输出时舍入（金额 HALF_UP 至 0.01、数量保留输入
  scale、比率/天数类保留 6 位小数）；
- 契约 ``ResultMetric.value`` 不支持 null，null 指标统一以字符串 ``"null"``
  输出并在 ``reason`` 字段标注原因，不伪造 0；
- ``AnalysisRequest.parameters`` 为 ``dict[str, Any]``（JSON 语义），统一在此
  做 Decimal/int 容错读取，避免各计算器重复处理字符串与数值两种形态。

M1 的 ``inventory_kpi`` 保留其模块内的等价私有实现（不改动已冻结并通过
黄金数据验收的代码），新增计算器统一使用本模块。
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from warehouse_engine.__version__ import FORMULA_VERSION
from warehouse_engine.contracts import ResultMetric

#: 金额输出精度（HALF_UP 至 0.01）
_MONEY_QUANTUM = Decimal("0.01")

#: 比率/天数类输出精度（6 位小数）
_RATIO_QUANTUM = Decimal("0.000001")

#: null 指标在契约 value（float|int|str）中的字符串表示
NULL_VALUE = "null"


def round_money(value: Decimal) -> str:
    """金额输出：HALF_UP 至 0.01。"""
    return str(value.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP))


def round_ratio(value: Decimal) -> str:
    """比率/天数输出：保留 6 位小数（HALF_UP）。"""
    return str(value.quantize(_RATIO_QUANTUM, rounding=ROUND_HALF_UP))


def build_metric(
    formula_id: str,
    metric_name: str,
    value: str,
    unit: str,
    sample_count: int,
    reason: str | None = None,
) -> ResultMetric:
    """构造携带公共字段（formula_id / formula_version / sample_count）的指标。"""
    return ResultMetric(
        name=metric_name,
        value=value,
        unit=unit,
        formula_id=formula_id,
        formula_version=FORMULA_VERSION,
        sample_count=sample_count,
        reason=reason,
    )


def param_decimal(
    parameters: Mapping[str, Any],
    key: str,
    default: Decimal | str,
) -> Decimal:
    """读取数值型参数（容忍 str / int / float / Decimal），缺失时取默认值。

    ``float`` 入参先经 ``str()`` 转换再交给 Decimal，避免二进制浮点尾数污染
    金额与阈值判定（§2.2 禁止 float 作为金额真值）。
    """
    raw = parameters.get(key)
    if raw is None:
        return Decimal(str(default))
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, float):
        return Decimal(str(raw))
    return Decimal(str(raw))


def param_int(parameters: Mapping[str, Any], key: str, default: int) -> int:
    """读取整数型参数（容忍 str / float），缺失时取默认值。"""
    raw = parameters.get(key)
    if raw is None:
        return default
    return int(param_decimal(parameters, key, Decimal(default)))


def param_int_tuple(
    parameters: Mapping[str, Any],
    key: str,
    default: tuple[int, ...],
) -> tuple[int, ...]:
    """读取整数序列参数（容忍逗号分隔字符串），缺失时取默认值。"""
    raw = parameters.get(key)
    if raw is None:
        return default
    if isinstance(raw, str):
        return tuple(int(item.strip()) for item in raw.split(",") if item.strip())
    return tuple(int(item) for item in raw)


def param_str_list(parameters: Mapping[str, Any], key: str) -> frozenset[str]:
    """读取字符串集合参数（如停产 SKU 清单），缺失时返回空集合。"""
    raw = parameters.get(key)
    if raw is None:
        return frozenset()
    if isinstance(raw, str):
        return frozenset(item.strip() for item in raw.split(",") if item.strip())
    return frozenset(str(item) for item in raw)


def sku_scalar(
    parameters: Mapping[str, Any],
    key: str,
    sku_id: str,
    default: Decimal | None,
) -> Decimal | None:
    """读取可按 SKU 覆盖的标量参数（标量或 ``{sku_id: value}`` 映射）。"""
    raw = parameters.get(key)
    if raw is None:
        return None if default is None else Decimal(str(default))
    if isinstance(raw, Mapping):
        value = raw.get(sku_id)
        if value is None:
            return None if default is None else Decimal(str(default))
        return Decimal(str(value))
    return Decimal(str(raw))
