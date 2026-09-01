"""补货计算器（F-REPL-001~003，formula-spec §7）。

实现要点：

- **公式（§7.1~§7.3）**：``SS = z × σ_d × √LT``、``ROP = d̄ × LT + SS``、
  ``Q* = max(0, ROP − closing_qty − on_order_qty)``；``d̄ = max(0, out_qty) /
  period_days``（F-KPI-003 出库量口径，与 F-KPI-008 一致）；
- **σ_d（§7.1）**：期间逐日需求的样本标准差（**n−1 分母，含零出库日**）。
  逐日需求取重放内核的 ``daily_out_by_sku``（已按 §7.1 逐日取
  ``max(0, 出库 + 报废 − 退货)``）；期间天数 < 2 时无离散样本，σ_d 记 0；
- **z 值表（§7.4）**：0.90→1.282、0.95→1.645、0.98→2.054、0.99→2.326；表外
  值取最近档位，差值相等时取较低档（确定性）。``√LT`` 一律用
  ``Decimal.sqrt()``，禁止 float 与 ``math.sqrt``；
- **参数优先级**：``dataset.replenishment[sku]`` → ``request.parameters``
  （标量或 ``{sku_id: value}`` 映射）→ 冻结默认值。``service_level`` 默认
  0.95（§7.4），``on_order_qty`` 默认 0；``lead_time_days`` 无默认值，缺失即
  非阻断 ``PARAM_MISSING``（fields: sku_id、缺失参数名），该 SKU 的
  SS/ROP/Q* 均为 null，其余分析继续（§7.5）；
- **边界（§7.5）**：``σ_d = 0 → SS = 0``、``d̄ = 0 → ROP = SS``，均照常输出；
  ``period_days = 0`` 时日均需求无定义，三项输出 ``"null"`` 并标注
  ``zero_period_days``；
- **输出粒度**：仅数据集级合计进 ``AnalysisResult.metrics``（每个 formula_id
  恰一个指标，unit 件），SKU 级 SS/ROP/Q* 与所用 z 值留在
  :class:`ReplenishmentResult` 内供测试断言，不新增契约字段；
- **确定性**：SKU 按字典序遍历，z 表最近档排序键为 ``(abs(差值), 档位)``，
  Warning 按 sku_id 排序产出。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from warehouse_engine.calculators._common import (
    NULL_VALUE,
    build_metric,
    sku_scalar,
)
from warehouse_engine.calculators.inventory_kpi import (
    InventoryKpiResult,
)
from warehouse_engine.calculators.inventory_kpi import (
    calculate as calculate_kpi,
)
from warehouse_engine.contracts import (
    AnalysisRequest,
    EngineDataset,
    ResultMetric,
    Warning,
    WarningSeverity,
)
from warehouse_engine.replay import ReplayOutcome, replay_movements

#: 本模块负责的公式 ID（补货口径，编号冻结于 docs/formula-spec.md）
FORMULA_IDS: tuple[str, ...] = (
    "F-REPL-001",  # 安全库存 safety_stock（SS = z·σ_d·√LT）
    "F-REPL-002",  # 补货点 reorder_point（ROP = d̄·LT + SS）
    "F-REPL-003",  # 建议补货量 suggested_qty（Q* = max(0, ROP − 库存 − 在途)）
)

#: 输出指标名（AnalysisResult.metrics[].name 的稳定标识）
METRIC_NAMES: dict[str, str] = {
    "F-REPL-001": "REPL.SAFETY_STOCK_TOTAL",
    "F-REPL-002": "REPL.REORDER_POINT_TOTAL",
    "F-REPL-003": "REPL.SUGGESTED_QTY_TOTAL",
}

#: 服务水平 → z 值表（§7.4，正态假设）
Z_TABLE: dict[Decimal, Decimal] = {
    Decimal("0.90"): Decimal("1.282"),
    Decimal("0.95"): Decimal("1.645"),
    Decimal("0.98"): Decimal("2.054"),
    Decimal("0.99"): Decimal("2.326"),
}

#: service_level 冻结默认值（§7.4）
DEFAULT_SERVICE_LEVEL = "0.95"

#: on_order_qty 冻结默认值（在途量缺省 0，§7.3）
DEFAULT_ON_ORDER_QTY = "0"


@dataclass(frozen=True)
class SkuReplenishment:
    """单个 SKU 的补货建议明细。"""

    sku_id: str
    #: 逐日需求样本标准差（n−1 分母，含零出库日）；期间 < 2 天时为 0
    sigma_d: Decimal | None
    #: 日均出库量 d̄ = max(0, out_qty) / period_days
    avg_daily_demand: Decimal | None
    lead_time_days: Decimal | None
    service_level: Decimal | None
    #: 实际使用的 z 值（表外 service_level 取最近档后的值）
    z: Decimal | None
    safety_stock: Decimal | None
    reorder_point: Decimal | None
    suggested_qty: Decimal | None
    on_order_qty: Decimal
    #: 缺失且无默认值可回退的参数名（§7.5 → PARAM_MISSING）
    missing_params: tuple[str, ...]
    #: 三项为 null 的原因：None / "param_missing" / "zero_period_days"
    reason: str | None


@dataclass(frozen=True)
class ReplenishmentResult:
    """补货计算输出：聚合指标、Warning 与 SKU 级明细。"""

    metrics: tuple[ResultMetric, ...]
    warnings: tuple[Warning, ...]
    per_sku: tuple[SkuReplenishment, ...]


def lookup_z(service_level: Decimal) -> Decimal:
    """按 §7.4 取 z 值：表内精确命中，表外取最近档（差值相等取较低档）。"""
    exact = Z_TABLE.get(service_level)
    if exact is not None:
        return exact
    return Z_TABLE[min(sorted(Z_TABLE), key=lambda level: (abs(service_level - level), level))]


def sample_std_dev(values: list[Decimal]) -> Decimal:
    """样本标准差（n−1 分母）；n < 2 时记 0（无离散样本，§7.1）。"""
    count = len(values)
    if count < 2:
        return Decimal(0)
    mean = sum(values, Decimal(0)) / Decimal(count)
    variance = sum(((value - mean) ** 2 for value in values), Decimal(0)) / Decimal(count - 1)
    return variance.sqrt()


def _daily_series(
    sku_id: str,
    request: AnalysisRequest,
    outcome: ReplayOutcome,
) -> list[Decimal]:
    """取 SKU 的逐日需求序列（无流水记录的 SKU 视为全零）。"""
    series = outcome.daily_out_by_sku.get(sku_id)
    if series is not None:
        return [value for _, value in series]
    day_count = max(0, (request.end_date - request.start_date).days)
    return [Decimal(0)] * day_count


def _sku_parameters(
    sku_id: str,
    request: AnalysisRequest,
    dataset: EngineDataset,
) -> tuple[Decimal | None, Decimal | None, Decimal, tuple[str, ...]]:
    """解析 SKU 的补货参数，返回 (lead_time_days, service_level, on_order_qty, 缺失参数名)。

    优先级：``dataset.replenishment[sku]`` → ``request.parameters``（标量或
    ``{sku_id: value}``）→ 冻结默认值（``service_level`` 0.95、``on_order_qty`` 0）。
    """
    record = next((row for row in dataset.replenishment if row.sku_id == sku_id), None)
    lead_time = record.lead_time_days if record is not None else None
    service_level = record.service_level if record is not None else None
    if lead_time is None:
        lead_time = sku_scalar(request.parameters, "lead_time_days", sku_id, None)
    if service_level is None:
        service_level = sku_scalar(
            request.parameters, "service_level", sku_id, Decimal(DEFAULT_SERVICE_LEVEL)
        )
    # on_order_qty 恒有冻结默认值 0（缺省即无在途量）
    on_order = sku_scalar(
        request.parameters, "on_order_qty", sku_id, Decimal(DEFAULT_ON_ORDER_QTY)
    )
    if on_order is None:
        on_order = Decimal(DEFAULT_ON_ORDER_QTY)
    missing = tuple(name for name, value in (("lead_time_days", lead_time),) if value is None)
    # service_level 有冻结默认值，正常路径不会缺失；保留检查以对齐 §7.5 原文
    if service_level is None:
        missing = (*missing, "service_level")
    return lead_time, service_level, on_order, missing


def calculate(
    request: AnalysisRequest,
    dataset: EngineDataset,
    *,
    kpi: InventoryKpiResult | None = None,
    outcome: ReplayOutcome | None = None,
) -> ReplenishmentResult:
    """按 formula-spec §7 计算 F-REPL-001~003（安全库存、补货点与建议量）。"""
    if kpi is None:
        kpi = calculate_kpi(request, dataset)
    if outcome is None:
        outcome = replay_movements(request, dataset.movements)

    period_days = (request.end_date - request.start_date).days
    rows: list[SkuReplenishment] = []
    warnings: list[Warning] = []
    totals: dict[str, Decimal] = {"F-REPL-001": Decimal(0), "F-REPL-002": Decimal(0), "F-REPL-003": Decimal(0)}
    counts: dict[str, int] = {"F-REPL-001": 0, "F-REPL-002": 0, "F-REPL-003": 0}
    reasons: dict[str, set[str]] = {formula_id: set() for formula_id in FORMULA_IDS}

    for sku_kpi in sorted(kpi.per_sku, key=lambda row: row.sku_id):
        sku_id = sku_kpi.sku_id
        lead_time, service_level, on_order, missing = _sku_parameters(sku_id, request, dataset)
        for name in missing:
            warnings.append(
                Warning(
                    code="PARAM_MISSING",
                    severity=WarningSeverity.WARN,
                    message=(
                        f"SKU {sku_id} 缺少补货参数 {name}，无法计算补货建议"
                        "（安全库存/补货点/建议量输出 null），其余分析继续。"
                    ),
                    fields=[sku_id, name],
                    blocking=False,
                )
            )

        z = None if service_level is None else lookup_z(service_level)
        if period_days <= 0:
            reason: str | None = "zero_period_days"
            sigma = None
            avg_daily = None
            safety_stock = None
            reorder_point = None
            suggested_qty = None
        elif missing:
            reason = "param_missing"
            sigma = None
            avg_daily = None
            safety_stock = None
            reorder_point = None
            suggested_qty = None
        else:
            reason = None
            assert lead_time is not None  # missing 为空时必然可取到
            assert z is not None
            series = _daily_series(sku_id, request, outcome)
            sigma = sample_std_dev(series)
            avg_daily = max(Decimal(0), sku_kpi.out_qty) / Decimal(period_days)
            # §7.1 / §7.5：σ_d = 0 → SS = 0；√LT 用 Decimal.sqrt()
            safety_stock = z * sigma * lead_time.sqrt()
            # §7.2 / §7.5：d̄ = 0 → ROP = SS
            reorder_point = avg_daily * lead_time + safety_stock
            # §7.3：Q* = max(0, ROP − 当前库存 − 在途量)
            suggested_qty = max(Decimal(0), reorder_point - sku_kpi.closing_qty - on_order)

        rows.append(
            SkuReplenishment(
                sku_id=sku_id,
                sigma_d=sigma,
                avg_daily_demand=avg_daily,
                lead_time_days=lead_time,
                service_level=service_level,
                z=z,
                safety_stock=safety_stock,
                reorder_point=reorder_point,
                suggested_qty=suggested_qty,
                on_order_qty=on_order,
                missing_params=missing,
                reason=reason,
            )
        )

        computed = (
            ("F-REPL-001", safety_stock),
            ("F-REPL-002", reorder_point),
            ("F-REPL-003", suggested_qty),
        )
        for formula_id, value in computed:
            if value is None:
                if reason is not None:
                    reasons[formula_id].add(reason)
                continue
            totals[formula_id] += value
            counts[formula_id] += 1

    metrics = tuple(
        build_metric(
            formula_id,
            METRIC_NAMES[formula_id],
            NULL_VALUE if counts[formula_id] == 0 else str(totals[formula_id]),
            "件",
            counts[formula_id],
            reason=_collapse_reason(reasons[formula_id], counts[formula_id]),
        )
        for formula_id in FORMULA_IDS
    )
    return ReplenishmentResult(
        metrics=metrics,
        warnings=tuple(warnings),
        per_sku=tuple(rows),
    )


def _collapse_reason(reasons: set[str], count: int) -> str | None:
    """汇总 null 原因：无有效值时给出唯一原因，混合原因时取字典序首项（确定性）。"""
    if count > 0 or not reasons:
        return None
    return min(reasons)
