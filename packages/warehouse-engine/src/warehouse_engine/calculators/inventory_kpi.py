"""库存 KPI / COGS 计算器（F-KPI-001~008、F-COGS-001，formula-spec §3）。

实现要点：

- 事件重放内核见 :mod:`warehouse_engine.replay`（排序、期间归属、冲销撤销、
  盘点替换、负库存与移动加权平均共享同一状态机）；
- 输出聚合口径：``AnalysisResult.metrics`` 为数据集级聚合值（各 SKU 级指标
  = 各仓库分桶之和，聚合值 = 各 SKU 之和；比率/天数类按 §3.7/§3.8 的
  总量口径计算），``sample_count`` 为参与统计的 SKU 数（F-KPI-004 为其
  自身定义的分母 SKU 数）；
- 单位成本口径（§3.5）：SKU 最近一次携带 ``unit_cost`` 的入库类事件
  （含期间开始前）；缺失时回退最近一条 ``SnapshotRecord.inventory_value``，
  两者均缺失时该 SKU 价值记 null、不计入合计，并发 ``UNIT_COST_MISSING``
  （fields 含 sku_id），SKU 不静默丢弃；
- 舍入（§2.2）：中间计算不舍入；输出时金额 HALF_UP 至 0.01，数量保留
  输入 scale（Decimal 精确算术结果），比率/天数类保留 6 位小数；
- ``lot_id`` 不参与 M1 计算；
- 期间与版本：指标随 ``AnalysisResult`` 携带数据期间
  （``period_start``/``period_end``）与 ``formula_version``；
- null 表示：契约 ``ResultMetric.value`` 不支持 null，null 指标以字符串
  ``"null"`` 输出并在 ``reason`` 字段标注原因，不伪造 0。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from warehouse_engine.__version__ import FORMULA_VERSION
from warehouse_engine.contracts import (
    AnalysisRequest,
    EngineDataset,
    ResultMetric,
    SnapshotRecord,
    Warning,
    WarningSeverity,
)
from warehouse_engine.replay import ReplayOutcome, replay_movements

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

#: 输出指标名（AnalysisResult.metrics[].name 的稳定标识）
METRIC_NAMES: dict[str, str] = {
    "F-KPI-001": "KPI.OPENING_QTY",
    "F-KPI-002": "KPI.CLOSING_QTY",
    "F-KPI-003": "KPI.OUT_QTY",
    "F-KPI-004": "KPI.ACTIVE_RATIO",
    "F-KPI-005": "KPI.INVENTORY_VALUE",
    "F-KPI-006": "KPI.TURNOVER",
    "F-KPI-007": "KPI.TURNOVER_DAYS",
    "F-KPI-008": "KPI.COVERAGE_DAYS",
    "F-COGS-001": "KPI.COGS",
}

#: 金额输出精度（HALF_UP 至 0.01）
_MONEY_QUANTUM = Decimal("0.01")

#: 比率/天数类输出精度（6 位小数）
_RATIO_QUANTUM = Decimal("0.000001")

#: null 指标在契约 value（float|int|str）中的字符串表示
_NULL_VALUE = "null"


def _round_money(value: Decimal) -> str:
    """金额输出：HALF_UP 至 0.01。"""
    return str(value.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP))


def _round_ratio(value: Decimal) -> str:
    """比率/天数输出：保留 6 位小数（HALF_UP）。"""
    return str(value.quantize(_RATIO_QUANTUM, rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class SkuKpi:
    """单个 SKU 的 KPI 汇总（各仓库分桶之和）。"""

    sku_id: str
    opening_qty: Decimal
    closing_qty: Decimal
    out_qty: Decimal
    #: 期间 COGS（任一桶期内负库存时为 0，§3.6 边界 1）
    cogs: Decimal
    #: §3.5 单位成本口径；None 表示缺失
    unit_cost: Decimal | None
    #: 库存价值；None 表示无法计量（不计入合计）
    inventory_value: Decimal | None


@dataclass(frozen=True)
class InventoryKpiResult:
    """KPI/COGS 计算输出：聚合指标、Warning 与 SKU 级明细。"""

    metrics: tuple[ResultMetric, ...]
    warnings: tuple[Warning, ...]
    per_sku: tuple[SkuKpi, ...]


def _scoped_snapshots(
    request: AnalysisRequest,
    snapshots: Sequence[SnapshotRecord],
) -> list[SnapshotRecord]:
    """仓库范围内的快照（warehouse_ids 为空视为不限仓库）。"""
    scope = set(request.warehouse_ids)
    if not scope:
        return list(snapshots)
    return [snapshot for snapshot in snapshots if snapshot.warehouse_id in scope]


def _latest_snapshot_by_sku(
    snapshots: Sequence[SnapshotRecord],
) -> dict[str, SnapshotRecord]:
    """每 SKU 取最近一条快照（snapshot_date 最新；同日取列表中靠后者，确定性）。"""
    latest: dict[str, SnapshotRecord] = {}
    for snapshot in snapshots:
        current = latest.get(snapshot.sku_id)
        if current is None or snapshot.snapshot_date >= current.snapshot_date:
            latest[snapshot.sku_id] = snapshot
    return latest


def _rollup_sku(outcome: ReplayOutcome) -> dict[str, SkuKpi]:
    """按 SKU 汇总桶级重放结果（含 COGS 期内负库存置零）。"""
    openings: dict[str, Decimal] = {}
    closings: dict[str, Decimal] = {}
    out_qtys: dict[str, Decimal] = {}
    cogs: dict[str, Decimal] = {}
    negative_skus: set[str] = set()
    for bucket in outcome.buckets:
        openings[bucket.sku_id] = openings.get(bucket.sku_id, Decimal(0)) + bucket.opening_qty
        closings[bucket.sku_id] = closings.get(bucket.sku_id, Decimal(0)) + bucket.closing_qty
        out_qtys[bucket.sku_id] = out_qtys.get(bucket.sku_id, Decimal(0)) + bucket.period_out_qty
        cogs[bucket.sku_id] = cogs.get(bucket.sku_id, Decimal(0)) + bucket.period_cogs
        if bucket.period_negative:
            negative_skus.add(bucket.sku_id)
    rollup: dict[str, SkuKpi] = {}
    for sku_id in sorted(openings):
        rollup[sku_id] = SkuKpi(
            sku_id=sku_id,
            opening_qty=openings[sku_id],
            closing_qty=closings[sku_id],
            out_qty=out_qtys[sku_id],
            # 期内任一时点余额 < 0：该 SKU 期间 COGS 记 0（§3.6 边界 1）
            cogs=Decimal(0) if sku_id in negative_skus else cogs[sku_id],
            unit_cost=outcome.last_cost_by_sku.get(sku_id),
            inventory_value=None,
        )
    return rollup


def _apply_inventory_value(
    rollup: dict[str, SkuKpi],
    latest_snapshot: dict[str, SnapshotRecord],
) -> set[str]:
    """填入 SKU 级库存价值（F-KPI-005），返回触发 UNIT_COST_MISSING 的 SKU。

    - ``closing_qty == 0``：价值为 0（无成本缺口）；
    - 单位成本已知：``closing_qty × unit_cost``；
    - 单位成本缺失：回退最近一条快照 ``inventory_value``；快照亦缺失时记 null。
    """
    missing_cost: set[str] = set()
    for sku_id, kpi in list(rollup.items()):
        if kpi.closing_qty == 0:
            rollup[sku_id] = SkuKpi(
                sku_id=sku_id,
                opening_qty=kpi.opening_qty,
                closing_qty=kpi.closing_qty,
                out_qty=kpi.out_qty,
                cogs=kpi.cogs,
                unit_cost=kpi.unit_cost,
                inventory_value=Decimal(0),
            )
            continue
        value: Decimal | None
        if kpi.unit_cost is not None:
            value = kpi.closing_qty * kpi.unit_cost
        else:
            missing_cost.add(sku_id)
            snapshot = latest_snapshot.get(sku_id)
            value = snapshot.inventory_value if snapshot is not None else None
        rollup[sku_id] = SkuKpi(
            sku_id=sku_id,
            opening_qty=kpi.opening_qty,
            closing_qty=kpi.closing_qty,
            out_qty=kpi.out_qty,
            cogs=kpi.cogs,
            unit_cost=kpi.unit_cost,
            inventory_value=value,
        )
    return missing_cost


def _metric(
    formula_id: str,
    value: str,
    unit: str,
    sample_count: int,
    reason: str | None = None,
) -> ResultMetric:
    """构造携带公共字段（formula_id/version/sample_count）的指标。"""
    return ResultMetric(
        name=METRIC_NAMES[formula_id],
        value=value,
        unit=unit,
        formula_id=formula_id,
        formula_version=FORMULA_VERSION,
        sample_count=sample_count,
        reason=reason,
    )


def calculate(
    request: AnalysisRequest,
    dataset: EngineDataset,
    *,
    outcome: ReplayOutcome | None = None,
) -> InventoryKpiResult:
    """按 formula-spec §3 计算 F-KPI-001~008 与 F-COGS-001。

    ``outcome`` 为 M2 新增的可选注入参数：``analyze`` 复用同一次重放结果传给
    五个计算器，避免重复重放；不传时（单测 / 独立调用）行为与 M1 完全一致。
    """
    if outcome is None:
        outcome = replay_movements(request, dataset.movements)
    snapshots = _scoped_snapshots(request, dataset.snapshots)
    latest_snapshot = _latest_snapshot_by_sku(snapshots)
    rollup = _rollup_sku(outcome)
    # 参与统计的 SKU：有范围内流水（含历史）或有范围内快照
    for sku_id in latest_snapshot:
        if sku_id not in rollup:
            rollup[sku_id] = SkuKpi(
                sku_id=sku_id,
                opening_qty=Decimal(0),
                closing_qty=Decimal(0),
                out_qty=Decimal(0),
                cogs=Decimal(0),
                unit_cost=outcome.last_cost_by_sku.get(sku_id),
                inventory_value=None,
            )
    missing_value_cost = _apply_inventory_value(rollup, latest_snapshot)

    sku_ids = sorted(rollup)
    sample_count = len(sku_ids)
    opening_total = sum((rollup[sku].opening_qty for sku in sku_ids), Decimal(0))
    closing_total = sum((rollup[sku].closing_qty for sku in sku_ids), Decimal(0))
    out_total = sum((rollup[sku].out_qty for sku in sku_ids), Decimal(0))
    cogs_total = sum((rollup[sku].cogs for sku in sku_ids), Decimal(0))
    value_total = Decimal(0)
    for sku in sku_ids:
        value = rollup[sku].inventory_value
        if value is not None:
            value_total += value

    # F-KPI-004 动销率：分母 = closing_qty > 0 或期间有事件的 SKU
    scope = set(request.warehouse_ids)
    period_event_skus = {
        movement.sku_id
        for movement in dataset.movements
        if request.start_date <= movement.move_date < request.end_date
        and (not scope or movement.warehouse_id in scope)
    }
    denominator = sum(
        1
        for sku in sku_ids
        if rollup[sku].closing_qty > 0 or sku in period_event_skus
    )
    numerator = sum(1 for sku in sku_ids if rollup[sku].out_qty > 0)

    # F-KPI-006/007 周转率与周转天数
    opening_value = Decimal(0)
    for sku in sku_ids:
        unit_cost = rollup[sku].unit_cost
        if unit_cost is not None:
            opening_value += rollup[sku].opening_qty * unit_cost
    closing_value = value_total  # F-KPI-005 口径（含快照回退）
    avg_inventory_value = (opening_value + closing_value) / 2
    period_days = (request.end_date - request.start_date).days

    metrics: list[ResultMetric] = []
    metrics.append(
        _metric("F-KPI-001", str(opening_total), "件", sample_count)
    )
    metrics.append(_metric("F-KPI-002", str(closing_total), "件", sample_count))
    metrics.append(_metric("F-KPI-003", str(out_total), "件", sample_count))
    if denominator == 0:
        metrics.append(
            _metric("F-KPI-004", _NULL_VALUE, "ratio", 0, reason="empty_dataset")
        )
    else:
        active_ratio = Decimal(numerator) / Decimal(denominator)
        metrics.append(
            _metric("F-KPI-004", _round_ratio(active_ratio), "ratio", denominator)
        )
    metrics.append(
        _metric("F-KPI-005", _round_money(value_total), "CNY", sample_count)
    )
    metrics.append(_metric("F-COGS-001", _round_money(cogs_total), "CNY", sample_count))

    turnover: Decimal | None = None
    turnover_reason: str | None = None
    if avg_inventory_value <= 0:
        turnover_reason = "avg_inventory_value_nonpositive"
    else:
        candidate = cogs_total / avg_inventory_value
        if candidate <= 0:
            turnover_reason = "turnover_nonpositive"
        else:
            turnover = candidate
    if turnover is None:
        metrics.append(
            _metric("F-KPI-006", _NULL_VALUE, "次/期间", sample_count, reason=turnover_reason)
        )
        metrics.append(
            _metric("F-KPI-007", _NULL_VALUE, "天", sample_count, reason=turnover_reason)
        )
    else:
        metrics.append(
            _metric("F-KPI-006", _round_ratio(turnover), "次/期间", sample_count)
        )
        metrics.append(
            _metric("F-KPI-007", _round_ratio(Decimal(period_days) / turnover), "天", sample_count)
        )

    # F-KPI-008 覆盖天数：daily_out = max(0, out_qty) / period_days
    if closing_total <= 0:
        metrics.append(
            _metric("F-KPI-008", _NULL_VALUE, "天", sample_count, reason="nonpositive_closing")
        )
    elif period_days == 0:
        metrics.append(
            _metric("F-KPI-008", _NULL_VALUE, "天", sample_count, reason="zero_period_days")
        )
    else:
        daily_out = (out_total if out_total > 0 else Decimal(0)) / Decimal(period_days)
        if daily_out == 0:
            metrics.append(
                _metric("F-KPI-008", _NULL_VALUE, "天", sample_count, reason="no_outflow")
            )
        else:
            metrics.append(
                _metric("F-KPI-008", _round_ratio(closing_total / daily_out), "天", sample_count)
            )

    # Warning：重放负库存 + 单位成本缺失（出库无成本记录 ∪ 价值回退缺失，按 SKU 去重）
    unit_cost_missing = missing_value_cost | set(outcome.cogs_missing_cost_skus)
    warnings: list[Warning] = list(outcome.warnings)
    warnings.extend(
        Warning(
            code="UNIT_COST_MISSING",
            severity=WarningSeverity.WARN,
            message=(
                f"SKU {sku_id} 缺失单位成本：出库成本按 0 计、库存价值回退快照口径，"
                "请补齐入库事件的 unit_cost。"
            ),
            fields=[sku_id],
            blocking=False,
        )
        for sku_id in sorted(unit_cost_missing)
    )
    return InventoryKpiResult(
        metrics=tuple(metrics),
        warnings=tuple(warnings),
        per_sku=tuple(rollup[sku] for sku in sku_ids),
    )
