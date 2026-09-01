"""ABC 分层、库龄与呆滞计算器（F-ABC-001 / F-AGE-001 / F-STALE-001，formula-spec §4~§6）。

实现要点：

- **依赖注入**：``calculate`` 接受可选的 ``kpi``（:class:`InventoryKpiResult`）
  与 ``outcome``（:class:`ReplayOutcome`）；``analyze`` 复用同一次 KPI 与重放
  结果，单测不传时模块内部自行计算，签名与 M1 的 ``calculate(request, dataset)``
  保持兼容；
- **ABC（§4）**：排序指标 ``amt = max(0, out_qty) × unit_cost``（单位成本取
  §3.5 口径），两级稳定排序（先 ``sku_id`` 升序、再 ``amt`` 降序）保证并列
  时按 ``sku_id`` 字典序、同输入必同输出；归属 ``cum ≤ A 阈值 → A``、
  ``A 阈值 < cum ≤ B 阈值 → B``、``cum > B 阈值 → C``，各 SKU 恰归一档；
  ``amt = 0`` 一律归 C 并发 ``NO_OUTFLOW``；``Σ amt = 0`` 时全部归 C 且每个
  SKU 各发一条 ``NO_OUTFLOW``；
- **库龄（§5）**：观察点 ``end_date``，区间左闭右开（边界值 30 天归
  ``[30,60)``）。有 ``lot_id`` 走批次桶（库龄 = 观察点 − 该批次最近一次入库
  日期）；无批次走 FIFO 残余切片加权平均并标注"近似"（``constituent``）；
  构成不足（盘点/负库存/冲销使残余合计 ≠ 期末余额）回退全历史入库
  （``all_history``）。期末库存 ≤ 0 不参与；期末 > 0 但全历史无入库事件 →
  跳过并发 ``DATE_MISSING``（fields 标注 ``no_inbound_event``）；
- **呆滞（§6）**：``no_outflow_days = (观察点 − 最后出库日期).days``，无出库
  记录时回退 ``(观察点 − 首次入库日期).days``；判定为
  ``no_outflow_days > stale_window_days`` **且** ``closing_qty > min_stock_qty``
  **且** 不满足排除条件（新品豁免 / 停产标记）。被排除的 SKU 在
  ``excluded_reason`` 标注原因，**不发 Warning**；
- **金额口径**：库龄与呆滞金额统一按 §3.5 SKU 级单位成本计算，缺失时金额记
  ``None``（不计入合计），不伪造金额；
- **输出粒度**：契约 ``AnalysisResult.metrics`` 只承载数据集级聚合（每个
  formula_id 恰一个指标），SKU 级明细留在 :class:`AbcAgingResult` 内供测试
  断言，不对外暴露、不新增契约字段；
- **确定性**：全部 ``dict`` 迭代走 ``sorted``，日期只用 ``date`` 与
  ``timedelta``，排序键不含浮点，Warning 按 ``sku_id`` 排序产出。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from warehouse_engine.calculators._common import (
    NULL_VALUE,
    build_metric,
    param_decimal,
    param_int,
    param_int_tuple,
    param_str_list,
    round_money,
    round_ratio,
)
from warehouse_engine.calculators.inventory_kpi import (
    InventoryKpiResult,
    SkuKpi,
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

#: 本模块负责的公式 ID（ABC/库龄/呆滞口径，编号冻结于 docs/formula-spec.md）
FORMULA_IDS: tuple[str, ...] = (
    "F-ABC-001",  # 期间 ABC 分类 classification
    "F-AGE-001",  # 期末库龄分布 inventory_age
    "F-STALE-001",  # 呆滞判定 stale_identification
)

#: 输出指标名（AnalysisResult.metrics[].name 的稳定标识）
METRIC_NAMES: dict[str, str] = {
    "F-ABC-001": "ABC.CLASSIFIED_SKU_COUNT",
    "F-AGE-001": "AGING.AVG_AGE_DAYS",
    "F-STALE-001": "STALE.AMOUNT",
}

#: 库龄分桶边界默认值（天，左闭右开）
DEFAULT_AGING_BUCKET_EDGES: tuple[int, ...] = (0, 30, 60, 90, 180)

#: 呆滞观察窗口默认值（天）
DEFAULT_STALE_WINDOW_DAYS = 90

#: 呆滞期末库存最低阈值默认值
DEFAULT_MIN_STOCK_QTY = 1

#: 新品豁免天数默认值
DEFAULT_NEW_INBOUND_GRACE_DAYS = 30


@dataclass(frozen=True)
class SkuAbc:
    """单个 SKU 的 ABC 分类明细。"""

    sku_id: str
    #: 排序指标（期间出库金额）；无法按金额计量时为 0
    amt: Decimal
    #: 排序后累计占比（Σamt 为 0 时为 0）
    cum_ratio: Decimal
    #: 排序序号（1 起）
    rank: int
    #: 归属档位："A" / "B" / "C"
    abc_class: str
    #: amt 为 0 的原因：None（有金额）/ "no_outflow" / "unit_cost_missing"
    zero_reason: str | None


@dataclass(frozen=True)
class SkuAging:
    """单条库龄记录（批次桶或无批次 SKU 级平均）。"""

    sku_id: str
    #: 批次口径的仓库；无批次平均口径为 None
    warehouse_id: str | None
    #: 批次口径的批次号；无批次为 None
    lot_id: str | None
    qty: Decimal
    age_days: Decimal
    #: 档位标签，如 "0-30" / "180+"
    bucket: str
    #: 库龄口径："lot" / "constituent" / "all_history"
    age_basis: str
    #: 该记录的库存金额（§3.5 单位成本口径）；成本缺失为 None
    amount: Decimal | None


@dataclass(frozen=True)
class SkuStale:
    """单个 SKU 的呆滞判定明细。"""

    sku_id: str
    closing_qty: Decimal
    #: 无出库天数；既无出库也无入库记录时为 None
    no_outflow_days: int | None
    is_stale: bool
    #: 未判呆滞的原因：None（已判呆滞）/ "new_inbound" / "discontinued" /
    #: "below_min_stock" / "within_window" / "no_outflow_record"
    excluded_reason: str | None
    #: 呆滞金额（closing_qty × §3.5 单位成本）；非呆滞或成本缺失为 None
    stale_amount: Decimal | None


@dataclass(frozen=True)
class AbcAgingResult:
    """ABC / 库龄 / 呆滞计算输出：聚合指标、Warning 与 SKU 级明细。"""

    metrics: tuple[ResultMetric, ...]
    warnings: tuple[Warning, ...]
    abc_rows: tuple[SkuAbc, ...]
    aging_rows: tuple[SkuAging, ...]
    stale_rows: tuple[SkuStale, ...]
    #: 库龄各档位数量分布（档位标签 → 数量）
    bucket_qty: dict[str, Decimal]
    #: 库龄各档位金额分布（档位标签 → 金额，成本缺失部分不计入）
    bucket_amount: dict[str, Decimal]
    #: 呆滞数量合计
    stale_qty: Decimal
    #: 呆滞率 = 呆滞金额 / 期末库存总价值；总价值为 0 时为 None
    stale_ratio: Decimal | None


def _bucket_labels(edges: tuple[int, ...]) -> tuple[str, ...]:
    """由分桶边界生成档位标签（如 (0,30,60) → ("0-30","30-60","60+")）。"""
    return tuple(
        f"{edge}-{edges[index + 1]}" if index + 1 < len(edges) else f"{edge}+"
        for index, edge in enumerate(edges)
    )


def _bucket_index(age_days: Decimal, edges: tuple[int, ...]) -> int:
    """左闭右开档位下标：恰为边界值时归入上一档的右邻档（30 天 → [30,60)）。"""
    index = 0
    for position, edge in enumerate(edges):
        if age_days >= Decimal(edge):
            index = position
    return index


def _classify_abc(
    sku_ids: list[str],
    out_qty: dict[str, Decimal],
    unit_cost: dict[str, Decimal | None],
    threshold_a: Decimal,
    threshold_b: Decimal,
) -> tuple[list[SkuAbc], set[str]]:
    """按 §4 排序与归属规则分类，返回 (分类明细, 需发 NO_OUTFLOW 的 SKU)。"""
    total_amt = Decimal(0)
    amt_by_sku: dict[str, Decimal] = {}
    zero_reason: dict[str, str | None] = {}
    for sku_id in sku_ids:
        cost = unit_cost.get(sku_id)
        quantity = max(Decimal(0), out_qty.get(sku_id, Decimal(0)))
        if cost is None:
            amt_by_sku[sku_id] = Decimal(0)
            zero_reason[sku_id] = "unit_cost_missing"
        elif quantity == 0:
            amt_by_sku[sku_id] = Decimal(0)
            zero_reason[sku_id] = "no_outflow"
        else:
            amt_by_sku[sku_id] = quantity * cost
            zero_reason[sku_id] = None
        total_amt += amt_by_sku[sku_id]

    # 两级稳定排序：先 sku_id 升序，再 amt 降序（并列时保持 sku_id 字典序）
    ordered = sorted(sku_ids)
    ordered.sort(key=lambda sku: -amt_by_sku[sku])

    rows: list[SkuAbc] = []
    no_outflow: set[str] = set()
    running = Decimal(0)
    for position, sku_id in enumerate(ordered, start=1):
        amt = amt_by_sku[sku_id]
        running += amt
        cum_ratio = running / total_amt if total_amt > 0 else Decimal(0)
        if amt == 0:
            abc_class = "C"
            no_outflow.add(sku_id)
        elif cum_ratio <= threshold_a:
            abc_class = "A"
        elif cum_ratio <= threshold_b:
            abc_class = "B"
        else:
            abc_class = "C"
        rows.append(
            SkuAbc(
                sku_id=sku_id,
                amt=amt,
                cum_ratio=cum_ratio,
                rank=position,
                abc_class=abc_class,
                zero_reason=zero_reason[sku_id],
            )
        )
    return rows, no_outflow


def _aging_rows_for_sku(
    sku_id: str,
    closing_qty: Decimal,
    unit_cost: Decimal | None,
    request: AnalysisRequest,
    outcome: ReplayOutcome,
    edges: tuple[int, ...],
    labels: tuple[str, ...],
) -> tuple[list[SkuAging], bool]:
    """计算单个 SKU 的库龄记录，返回 (记录列表, 是否缺少入库事件)。

    口径优先级（§5）：批次桶 → FIFO 残余切片（构成期末余额）→ 全历史入库。
    """
    observation = request.end_date
    lots = [lot for lot in outcome.lot_balances if lot.sku_id == sku_id and lot.qty > 0]
    entries: list[tuple[str | None, str | None, Decimal, Decimal, str]] = []
    if lots:
        for lot in sorted(lots, key=lambda lot: (lot.warehouse_id, lot.lot_id)):
            age = Decimal((observation - lot.last_inbound_date).days)
            entries.append((lot.warehouse_id, lot.lot_id, lot.qty, age, "lot"))
    else:
        residual = outcome.residual_inbound_by_sku.get(sku_id, ())
        residual_total = sum((lot.qty for lot in residual), Decimal(0))
        if residual and residual_total == closing_qty:
            for inbound in residual:
                age = Decimal((observation - inbound.move_date).days)
                entries.append((None, None, inbound.qty, age, "constituent"))
        else:
            aggregate = outcome.all_inbound_by_sku.get(sku_id)
            if aggregate is None or aggregate.total_qty <= 0:
                return [], True
            # O(1) 内存聚合换算：avg_age = (E×Σqty − Σ(qty×ordinal)) / Σqty
            ordinal = Decimal(observation.toordinal())
            avg_age = (ordinal * aggregate.total_qty - aggregate.weighted_ordinal) / (
                aggregate.total_qty
            )
            entries.append((None, None, closing_qty, avg_age, "all_history"))

    rows = [
        SkuAging(
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            lot_id=lot_id,
            qty=qty,
            age_days=age,
            bucket=labels[_bucket_index(age, edges)],
            age_basis=basis,
            amount=None if unit_cost is None else qty * unit_cost,
        )
        for warehouse_id, lot_id, qty, age, basis in entries
    ]
    return rows, False


def _no_outflow_days(
    sku_id: str, observation: date, outcome: ReplayOutcome
) -> int | None:
    """无出库天数（§6）：优先末次出库日期，无出库记录时回退首次入库日期。"""
    last_outbound = outcome.last_outbound_date_by_sku.get(sku_id)
    if last_outbound is not None:
        return (observation - last_outbound).days
    first_inbound = outcome.first_inbound_date_by_sku.get(sku_id)
    if first_inbound is not None:
        return (observation - first_inbound).days
    return None


def calculate(
    request: AnalysisRequest,
    dataset: EngineDataset,
    *,
    kpi: InventoryKpiResult | None = None,
    outcome: ReplayOutcome | None = None,
) -> AbcAgingResult:
    """按 formula-spec §4~§6 计算 F-ABC-001、F-AGE-001 与 F-STALE-001。"""
    if kpi is None:
        kpi = calculate_kpi(request, dataset)
    if outcome is None:
        outcome = replay_movements(request, dataset.movements)

    parameters = request.parameters
    threshold_a = param_decimal(parameters, "abc_threshold_a", "0.80")
    threshold_b = param_decimal(parameters, "abc_threshold_b", "0.95")
    edges = param_int_tuple(parameters, "aging_bucket_edges", DEFAULT_AGING_BUCKET_EDGES)
    labels = _bucket_labels(edges)
    stale_window_days = param_int(parameters, "stale_window_days", DEFAULT_STALE_WINDOW_DAYS)
    min_stock_qty = param_decimal(parameters, "min_stock_qty", str(DEFAULT_MIN_STOCK_QTY))
    grace_days = param_int(
        parameters, "new_inbound_grace_days", DEFAULT_NEW_INBOUND_GRACE_DAYS
    )
    discontinued_skus = param_str_list(parameters, "discontinued_skus")

    per_sku: dict[str, SkuKpi] = {row.sku_id: row for row in kpi.per_sku}
    sku_ids = sorted(per_sku)

    # --- F-ABC-001 ---
    out_qty = {sku_id: per_sku[sku_id].out_qty for sku_id in sku_ids}
    unit_cost = {sku_id: per_sku[sku_id].unit_cost for sku_id in sku_ids}
    abc_rows, no_outflow = _classify_abc(
        sku_ids, out_qty, unit_cost, threshold_a, threshold_b
    )
    total_amt = sum((row.amt for row in abc_rows), Decimal(0))
    zero_total = total_amt == 0 and bool(sku_ids)
    abc_metric = build_metric(
        "F-ABC-001",
        METRIC_NAMES["F-ABC-001"],
        str(len(abc_rows)),
        "SKU",
        len(abc_rows),
        reason="zero_total_amount" if zero_total else None,
    )

    # --- F-AGE-001 ---
    observation = request.end_date
    aging_rows: list[SkuAging] = []
    missing_date_skus: list[str] = []
    for sku_id in sku_ids:
        closing_qty = per_sku[sku_id].closing_qty
        if closing_qty <= 0:  # §5 边界 2：期末库存为 0 不参与库龄分布
            continue
        rows, missing = _aging_rows_for_sku(
            sku_id, closing_qty, unit_cost.get(sku_id), request, outcome, edges, labels
        )
        if missing:  # §5 边界 3：期末 > 0 但全历史无入库事件
            missing_date_skus.append(sku_id)
            continue
        aging_rows.extend(rows)

    bucket_qty: dict[str, Decimal] = {label: Decimal(0) for label in labels}
    bucket_amount: dict[str, Decimal] = {label: Decimal(0) for label in labels}
    weighted_age = Decimal(0)
    total_age_qty = Decimal(0)
    for row in aging_rows:
        bucket_qty[row.bucket] = bucket_qty.get(row.bucket, Decimal(0)) + row.qty
        if row.amount is not None:
            bucket_amount[row.bucket] = bucket_amount.get(row.bucket, Decimal(0)) + row.amount
        weighted_age += row.qty * row.age_days
        total_age_qty += row.qty
    if total_age_qty > 0:
        aging_metric = build_metric(
            "F-AGE-001",
            METRIC_NAMES["F-AGE-001"],
            round_ratio(weighted_age / total_age_qty),
            "天",
            len(aging_rows),
        )
    else:
        aging_metric = build_metric(
            "F-AGE-001",
            METRIC_NAMES["F-AGE-001"],
            NULL_VALUE,
            "天",
            0,
            reason="no_closing_stock",
        )

    # --- F-STALE-001 ---
    grace_cutoff = observation - timedelta(days=stale_window_days + grace_days)
    stale_rows: list[SkuStale] = []
    stale_qty = Decimal(0)
    stale_amount = Decimal(0)
    for sku_id in sku_ids:
        closing_qty = per_sku[sku_id].closing_qty
        days = _no_outflow_days(sku_id, observation, outcome)
        first_inbound = outcome.first_inbound_date_by_sku.get(sku_id)
        if sku_id in discontinued_skus:
            reason = "discontinued"
        elif first_inbound is not None and first_inbound > grace_cutoff:
            reason = "new_inbound"
        elif closing_qty <= min_stock_qty:
            reason = "below_min_stock"
        elif days is None:
            reason = "no_outflow_record"
        elif days <= stale_window_days:
            reason = "within_window"
        else:
            reason = None
        is_stale = reason is None
        cost = unit_cost.get(sku_id)
        amount = closing_qty * cost if (is_stale and cost is not None) else None
        if is_stale:
            stale_qty += closing_qty
            if amount is not None:
                stale_amount += amount
        stale_rows.append(
            SkuStale(
                sku_id=sku_id,
                closing_qty=closing_qty,
                no_outflow_days=days,
                is_stale=is_stale,
                excluded_reason=reason,
                stale_amount=amount,
            )
        )

    total_value = sum(
        (row.inventory_value for row in kpi.per_sku if row.inventory_value is not None),
        Decimal(0),
    )
    stale_ratio = stale_amount / total_value if total_value > 0 else None
    stale_metric = build_metric(
        "F-STALE-001",
        METRIC_NAMES["F-STALE-001"],
        round_money(stale_amount),
        "CNY",
        sum(1 for row in stale_rows if row.is_stale),
    )

    # --- Warning（按 sku_id 排序，NO_OUTFLOW 先于 DATE_MISSING，确定性）---
    warnings: list[Warning] = [
        Warning(
            code="NO_OUTFLOW",
            severity=WarningSeverity.WARN,
            message=(
                f"SKU {sku_id} 期间无出库金额（{'缺少单位成本' if per_sku[sku_id].unit_cost is None else '期间无出库或净退货'}），"
                "ABC 分类归为 C 类。"
            ),
            fields=[sku_id],
            blocking=False,
        )
        for sku_id in sorted(no_outflow)
    ]
    warnings.extend(
        Warning(
            code="DATE_MISSING",
            severity=WarningSeverity.WARN,
            message=(
                f"SKU {sku_id} 期末有库存但全历史无入库事件，无法计算库龄，"
                "已跳过该 SKU（不猜测、不静默修正日期）。"
            ),
            fields=[sku_id, "no_inbound_event"],
            blocking=False,
        )
        for sku_id in sorted(missing_date_skus)
    )

    return AbcAgingResult(
        metrics=(abc_metric, aging_metric, stale_metric),
        warnings=tuple(warnings),
        abc_rows=tuple(abc_rows),
        aging_rows=tuple(aging_rows),
        stale_rows=tuple(stale_rows),
        bucket_qty=bucket_qty,
        bucket_amount=bucket_amount,
        stale_qty=stale_qty,
        stale_ratio=stale_ratio,
    )
