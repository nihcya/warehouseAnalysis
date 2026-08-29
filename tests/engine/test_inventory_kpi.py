"""KPI/COGS 计算器测试（F-KPI-001~008、F-COGS-001，formula-spec §3）。

按公式逐条覆盖：期初/期末（含盘点替换）、出库量（退货冲减、调拨不计入）、
动销率、库存价值（UNIT_COST_MISSING 回退）、COGS（移动加权平均与三类边界）、
周转率/周转天数、覆盖天数（样本不足降级）、舍入与公共字段。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from contracts import (
    AnalysisRequest,
    EngineDataset,
    EventSource,
    MovementRecord,
    SkuRecord,
    SnapshotRecord,
)
from warehouse_engine.calculators import inventory_kpi

REQUEST = AnalysisRequest(
    run_id="run-kpi-0001",
    start_date=date(2026, 6, 1),
    end_date=date(2026, 6, 30),
    warehouse_ids=["WH-01"],
)


def _sku(sku_id: str, unit_cost: str | None = "1.20") -> SkuRecord:
    return SkuRecord(
        sku_id=sku_id,
        name=f"SKU {sku_id}",
        category="饮料",
        unit="瓶",
        unit_cost=Decimal(unit_cost) if unit_cost is not None else None,
    )


def _movement(
    event_id: str,
    sku_id: str,
    move_type: str,
    move_date: date,
    quantity: str,
    *,
    warehouse_id: str = "WH-01",
    unit_cost: str | None = None,
    reversal_of: str | None = None,
) -> MovementRecord:
    return MovementRecord(
        event_id=event_id,
        sku_id=sku_id,
        move_type=move_type,
        quantity=Decimal(quantity),
        move_date=move_date,
        occurred_at=datetime(move_date.year, move_date.month, move_date.day, 8, 0, tzinfo=UTC),
        warehouse_id=warehouse_id,
        unit_cost=Decimal(unit_cost) if unit_cost is not None else None,
        source=EventSource.IMPORT,
        reversal_of=reversal_of,
    )


def _snapshot(
    sku_id: str,
    quantity: str,
    inventory_value: str | None,
    *,
    snapshot_date: date = date(2026, 6, 30),
    warehouse_id: str = "WH-01",
) -> SnapshotRecord:
    return SnapshotRecord(
        sku_id=sku_id,
        snapshot_date=snapshot_date,
        quantity=Decimal(quantity),
        warehouse_id=warehouse_id,
        inventory_value=Decimal(inventory_value) if inventory_value is not None else None,
    )


def _calculate(movements, skus=None, snapshots=(), request=REQUEST):
    dataset = EngineDataset(
        skus=list(skus) if skus is not None else [_sku("SKU-0001")],
        movements=list(movements),
        snapshots=list(snapshots),
    )
    return inventory_kpi.calculate(request, dataset)


def _metric_by_id(result, formula_id: str):
    found = [m for m in result.metrics if m.formula_id == formula_id]
    assert len(found) == 1, f"指标 {formula_id} 应恰好出现一次"
    return found[0]


def _per_sku(result, sku_id: str) -> inventory_kpi.SkuKpi:
    found = [kpi for kpi in result.per_sku if kpi.sku_id == sku_id]
    assert len(found) == 1
    return found[0]


# ---------------------------------------------------------------- F-KPI-001


def test_f_kpi_001_opening_from_history() -> None:
    """期初 = 历史事件重放余额（无历史时为 0）。"""
    result = _calculate(
        [
            _movement("EVT-H1", "SKU-0001", "INBOUND", date(2026, 5, 10), "10"),
            _movement("EVT-H2", "SKU-0001", "OUTBOUND", date(2026, 5, 20), "4"),
            _movement("EVT-P", "SKU-0001", "OUTBOUND", date(2026, 6, 10), "2"),
        ]
    )
    assert _metric_by_id(result, "F-KPI-001").value == "6"
    assert _per_sku(result, "SKU-0001").opening_qty == Decimal(6)


def test_f_kpi_001_opening_with_historical_stocktake() -> None:
    """历史盘点参与期初重放：期初 = 替换后的余额。"""
    result = _calculate(
        [
            _movement("EVT-H1", "SKU-0001", "INBOUND", date(2026, 5, 10), "10"),
            _movement("EVT-S", "SKU-0001", "STOCKTAKE", date(2026, 5, 20), "8"),
        ]
    )
    assert _metric_by_id(result, "F-KPI-001").value == "8"


# ---------------------------------------------------------------- F-KPI-002


def test_f_kpi_002_closing_with_period_stocktake() -> None:
    """期末含期内盘点替换：closing = 盘点实盘值。"""
    result = _calculate(
        [
            _movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "10"),
            _movement("EVT-S", "SKU-0001", "STOCKTAKE", date(2026, 6, 20), "3"),
        ]
    )
    assert _metric_by_id(result, "F-KPI-002").value == "3"


def test_f_kpi_002_negative_closing_output_as_is() -> None:
    """期末为负：原样输出并伴随 NEGATIVE_BALANCE Warning。"""
    result = _calculate(
        [_movement("EVT-1", "SKU-0001", "OUTBOUND", date(2026, 6, 5), "5")]
    )
    assert _metric_by_id(result, "F-KPI-002").value == "-5"
    codes = [w.code for w in result.warnings]
    assert "NEGATIVE_BALANCE" in codes


def test_f_kpi_002_sku_level_is_sum_of_warehouses() -> None:
    """SKU 级期末 = 各仓分桶之和（空 warehouse_ids 视为不限仓库）。"""
    request = AnalysisRequest(
        run_id="run-kpi-0002",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 30),
        warehouse_ids=[],
    )
    result = _calculate(
        [
            _movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "10", warehouse_id="WH-01"),
            _movement("EVT-2", "SKU-0001", "INBOUND", date(2026, 6, 3), "7", warehouse_id="WH-02"),
        ],
        request=request,
    )
    assert _metric_by_id(result, "F-KPI-002").value == "17"


# ---------------------------------------------------------------- F-KPI-003


def test_f_kpi_003_out_qty_with_scrap_and_return() -> None:
    """出库量 = Σ出库 + Σ报废 − Σ退货；调拨不计入。"""
    result = _calculate(
        [
            _movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "50", unit_cost="2.00"),
            _movement("EVT-2", "SKU-0001", "OUTBOUND", date(2026, 6, 5), "10"),
            _movement("EVT-3", "SKU-0001", "SCRAP", date(2026, 6, 8), "3"),
            _movement("EVT-4", "SKU-0001", "RETURN", date(2026, 6, 10), "4"),
            _movement("EVT-5", "SKU-0001", "TRANSFER", date(2026, 6, 12), "10"),
        ]
    )
    assert _metric_by_id(result, "F-KPI-003").value == "9"  # 10 + 3 − 4


def test_f_kpi_003_negative_out_qty_output_as_is() -> None:
    """净退货场景（退货大于出库）：出库量为负，原样输出。"""
    result = _calculate(
        [
            _movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "50", unit_cost="2.00"),
            _movement("EVT-2", "SKU-0001", "OUTBOUND", date(2026, 6, 5), "3"),
            _movement("EVT-3", "SKU-0001", "RETURN", date(2026, 6, 10), "7"),
        ]
    )
    assert _metric_by_id(result, "F-KPI-003").value == "-4"


# ---------------------------------------------------------------- F-KPI-004


def test_f_kpi_004_active_ratio_denominator_definition() -> None:
    """动销率 = |out_qty>0 的 SKU| / |closing>0 或期间有事件的 SKU|。"""
    skus = [_sku("SKU-A"), _sku("SKU-B"), _sku("SKU-C"), _sku("SKU-D")]
    movements = [
        # A：期间有事件且 out > 0 → 分子
        _movement("EVT-A1", "SKU-A", "INBOUND", date(2026, 6, 2), "10", unit_cost="1.00"),
        _movement("EVT-A2", "SKU-A", "OUTBOUND", date(2026, 6, 5), "4"),
        # B：期间有事件但纯入库（closing>0、out=0）→ 仅分母
        _movement("EVT-B1", "SKU-B", "INBOUND", date(2026, 6, 3), "5", unit_cost="1.00"),
        # C：期间有事件且冲销后 closing=0、out=0 → 仅分母（期间有事件）
        _movement("EVT-C1", "SKU-C", "INBOUND", date(2026, 6, 4), "6", unit_cost="1.00"),
        _movement("EVT-C2", "SKU-C", "REVERSAL", date(2026, 6, 6), "6", reversal_of="EVT-C1"),
        # D：无任何事件（master 数据孤立 SKU，closing=0）→ 不进分母
    ]
    result = _calculate(movements, skus=skus)
    metric = _metric_by_id(result, "F-KPI-004")
    assert metric.value == "0.333333"  # 1/3
    assert metric.sample_count == 3


def test_f_kpi_004_empty_dataset_outputs_null() -> None:
    """空数据集：动销率输出 null、sample_count=0、不发 Warning。"""
    result = _calculate([], skus=[], snapshots=[])
    metric = _metric_by_id(result, "F-KPI-004")
    assert metric.value == "null"
    assert metric.sample_count == 0
    assert metric.reason == "empty_dataset"
    assert all(w.code != "EMPTY_DATASET" for w in result.warnings)


# ---------------------------------------------------------------- F-KPI-005


def test_f_kpi_005_value_uses_latest_inbound_cost_including_history() -> None:
    """单位成本口径：最近一次携带 unit_cost 的入库（含期间开始前）。"""
    result = _calculate(
        [
            _movement("EVT-H", "SKU-0001", "INBOUND", date(2026, 5, 10), "10", unit_cost="1.00"),
            _movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "10", unit_cost="2.00"),
        ]
    )
    assert _metric_by_id(result, "F-KPI-005").value == "40.00"  # 20 × 2.00


def test_f_kpi_005_snapshot_fallback_with_warning() -> None:
    """unit_cost 缺失：回退最近快照 inventory_value 并发 UNIT_COST_MISSING。"""
    result = _calculate(
        [_movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "10")],
        snapshots=[
            _snapshot("SKU-0001", "9", "18.00", snapshot_date=date(2026, 6, 10)),
            _snapshot("SKU-0001", "10", "25.00", snapshot_date=date(2026, 6, 20)),
        ],
    )
    assert _metric_by_id(result, "F-KPI-005").value == "25.00"
    warnings = [w for w in result.warnings if w.code == "UNIT_COST_MISSING"]
    assert len(warnings) == 1
    assert "SKU-0001" in warnings[0].fields


def test_f_kpi_005_both_missing_null_excluded_from_total() -> None:
    """unit_cost 与快照均缺失：该 SKU 价值记 null、不计入合计，SKU 不静默丢弃。"""
    skus = [_sku("SKU-A", unit_cost="2.00"), _sku("SKU-B", unit_cost=None)]
    movements = [
        _movement("EVT-A", "SKU-A", "INBOUND", date(2026, 6, 2), "10", unit_cost="2.00"),
        _movement("EVT-B", "SKU-B", "INBOUND", date(2026, 6, 3), "5"),
    ]
    result = _calculate(movements, skus=skus)
    # 仅 SKU-A 计入合计：10 × 2.00
    assert _metric_by_id(result, "F-KPI-005").value == "20.00"
    assert _per_sku(result, "SKU-B").inventory_value is None
    warnings = [w for w in result.warnings if w.code == "UNIT_COST_MISSING"]
    assert len(warnings) == 1
    assert warnings[0].fields == ["SKU-B"]


def test_f_kpi_005_zero_closing_value_is_zero_without_warning() -> None:
    """closing=0：价值为 0（无成本缺口，不发 UNIT_COST_MISSING）。"""
    result = _calculate(
        [
            _movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "5", unit_cost="2.00"),
            _movement("EVT-2", "SKU-0001", "OUTBOUND", date(2026, 6, 5), "5"),
        ]
    )
    assert _metric_by_id(result, "F-KPI-005").value == "0.00"
    assert all(w.code != "UNIT_COST_MISSING" for w in result.warnings)


# ---------------------------------------------------------------- F-COGS-001


def test_f_cogs_001_moving_weighted_average() -> None:
    """多次入库不同成本：出库按移动加权平均计 COGS。"""
    result = _calculate(
        [
            _movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "10", unit_cost="2.00"),
            _movement("EVT-2", "SKU-0001", "INBOUND", date(2026, 6, 3), "10", unit_cost="4.00"),
            _movement("EVT-3", "SKU-0001", "OUTBOUND", date(2026, 6, 5), "10"),
        ]
    )
    # avg = 3.00 → COGS = 30.00
    assert _metric_by_id(result, "F-COGS-001").value == "30.00"


def test_f_cogs_001_return_offset() -> None:
    """退货按当前 avg_cost 冲减 COGS。"""
    result = _calculate(
        [
            _movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "10", unit_cost="2.00"),
            _movement("EVT-2", "SKU-0001", "OUTBOUND", date(2026, 6, 5), "6"),
            _movement("EVT-3", "SKU-0001", "RETURN", date(2026, 6, 8), "2"),
        ]
    )
    assert _metric_by_id(result, "F-COGS-001").value == "8.00"  # 6×2 − 2×2


def test_f_cogs_001_negative_inventory_zeroes_sku_cogs() -> None:
    """期内负库存：该 SKU 期间 COGS 记 0，其他 SKU 不受影响。"""
    skus = [_sku("SKU-A"), _sku("SKU-B")]
    movements = [
        # SKU-A：期内出现负余额
        _movement("EVT-A1", "SKU-A", "OUTBOUND", date(2026, 6, 5), "5"),
        _movement("EVT-A2", "SKU-A", "INBOUND", date(2026, 6, 10), "8", unit_cost="2.00"),
        _movement("EVT-A3", "SKU-A", "OUTBOUND", date(2026, 6, 15), "3"),
        # SKU-B：正常周转
        _movement("EVT-B1", "SKU-B", "INBOUND", date(2026, 6, 2), "10", unit_cost="3.00"),
        _movement("EVT-B2", "SKU-B", "OUTBOUND", date(2026, 6, 6), "4"),
    ]
    result = _calculate(movements, skus=skus)
    # SKU-A 负库存 → COGS=0；SKU-B 正常 4×3=12.00；合计仅 B
    assert _metric_by_id(result, "F-COGS-001").value == "12.00"
    assert _per_sku(result, "SKU-A").cogs == Decimal(0)


def test_f_cogs_001_missing_cost_records_zero() -> None:
    """出库时无任何已知成本记录：该笔 COGS 记 0 并发 UNIT_COST_MISSING。"""
    result = _calculate(
        [
            _movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "10"),
            _movement("EVT-2", "SKU-0001", "OUTBOUND", date(2026, 6, 5), "4"),
        ]
    )
    assert _metric_by_id(result, "F-COGS-001").value == "0.00"
    warnings = [w for w in result.warnings if w.code == "UNIT_COST_MISSING"]
    assert len(warnings) == 1
    assert "SKU-0001" in warnings[0].fields


def test_f_cogs_001_reversal_nets_out_period_contribution() -> None:
    """期内冲销期内出库：出库量与 COGS 等额冲回。"""
    result = _calculate(
        [
            _movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "10", unit_cost="2.00"),
            _movement("EVT-2", "SKU-0001", "OUTBOUND", date(2026, 6, 5), "6"),
            _movement("EVT-R", "SKU-0001", "REVERSAL", date(2026, 6, 10), "6", reversal_of="EVT-2"),
        ]
    )
    assert _metric_by_id(result, "F-COGS-001").value == "0.00"
    assert _metric_by_id(result, "F-KPI-003").value == "0"


# ------------------------------------------------- F-KPI-006 / F-KPI-007


def test_f_kpi_006_007_turnover_and_days() -> None:
    """周转率 = COGS / 平均库存价值；周转天数 = period_days / turnover。"""
    result = _calculate(
        [
            _movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "10", unit_cost="2.00"),
            _movement("EVT-2", "SKU-0001", "OUTBOUND", date(2026, 6, 5), "5"),
        ]
    )
    # opening=0、closing=5×2=10 → avg=5；COGS=10 → turnover=2
    assert _metric_by_id(result, "F-KPI-006").value == "2.000000"
    # period_days = 29 → 29/2 = 14.5
    assert _metric_by_id(result, "F-KPI-007").value == "14.500000"


def test_f_kpi_006_007_null_when_avg_inventory_nonpositive() -> None:
    """平均库存价值 <= 0：周转率/天数输出 null 并标注原因，不伪造 0。"""
    result = _calculate(
        [_movement("EVT-1", "SKU-0001", "OUTBOUND", date(2026, 6, 5), "5")]
    )
    turnover = _metric_by_id(result, "F-KPI-006")
    days = _metric_by_id(result, "F-KPI-007")
    assert turnover.value == "null"
    assert turnover.reason == "avg_inventory_value_nonpositive"
    assert days.value == "null"
    assert days.reason == "avg_inventory_value_nonpositive"


def test_f_kpi_006_007_null_when_turnover_nonpositive() -> None:
    """COGS<=0 且库存价值为正：turnover<=0 → null 并标注原因。"""
    result = _calculate(
        [
            _movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "10", unit_cost="2.00"),
            _movement("EVT-2", "SKU-0001", "RETURN", date(2026, 6, 10), "3"),
        ]
    )
    # 无出库只有退货：COGS = −3×2 < 0 → turnover < 0
    turnover = _metric_by_id(result, "F-KPI-006")
    assert turnover.value == "null"
    assert turnover.reason == "turnover_nonpositive"


# ---------------------------------------------------------------- F-KPI-008


def test_f_kpi_008_coverage_days() -> None:
    """覆盖天数 = closing / (max(0, out_qty)/period_days)。"""
    result = _calculate(
        [
            _movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "10", unit_cost="2.00"),
            _movement("EVT-2", "SKU-0001", "OUTBOUND", date(2026, 6, 5), "5"),
        ]
    )
    # daily_out = 5/29；coverage = 5 / (5/29) = 29
    assert _metric_by_id(result, "F-KPI-008").value == "29.000000"


def test_f_kpi_008_null_when_no_outflow() -> None:
    """期间无出库（daily_out=0）：覆盖天数输出 null 并标注原因。"""
    result = _calculate(
        [_movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "10", unit_cost="2.00")]
    )
    metric = _metric_by_id(result, "F-KPI-008")
    assert metric.value == "null"
    assert metric.reason == "no_outflow"


def test_f_kpi_008_null_when_closing_nonpositive() -> None:
    """closing <= 0：覆盖天数输出 null。"""
    result = _calculate(
        [_movement("EVT-1", "SKU-0001", "OUTBOUND", date(2026, 6, 5), "5")]
    )
    metric = _metric_by_id(result, "F-KPI-008")
    assert metric.value == "null"
    assert metric.reason == "nonpositive_closing"


# ----------------------------------------------------- 舍入与公共字段


def test_rounding_money_half_up_and_ratio_six_decimals() -> None:
    """金额 HALF_UP 至 0.01；比率/天数保留 6 位小数。"""
    result = _calculate(
        [
            _movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "3", unit_cost="1.005"),
            _movement("EVT-2", "SKU-0001", "OUTBOUND", date(2026, 6, 5), "1"),
        ]
    )
    # COGS = 1 × 1.005 = 1.005 → HALF_UP → 1.01（注意：1.005 scale=3 违反精度，
    # 本用例直接调用计算器绕过校验，仅验证舍入口径）
    assert _metric_by_id(result, "F-COGS-001").value == "1.01"
    turnover = _metric_by_id(result, "F-KPI-006")
    assert len(turnover.value.split(".")[1]) == 6


def test_quantity_keeps_input_scale() -> None:
    """数量类指标保留输入 scale（0.5 不会被格式化为 0.50 或 0.5e0）。"""
    result = _calculate(
        [
            _movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "10.5", unit_cost="2.00"),
            _movement("EVT-2", "SKU-0001", "OUTBOUND", date(2026, 6, 5), "0.5"),
        ]
    )
    assert _metric_by_id(result, "F-KPI-002").value == "10.0"
    assert _metric_by_id(result, "F-KPI-003").value == "0.5"


def test_common_fields_on_every_metric() -> None:
    """公共字段：formula_id、formula_version="0.1.0"、sample_count 齐全。"""
    result = _calculate(
        [
            _movement("EVT-1", "SKU-0001", "INBOUND", date(2026, 6, 2), "10", unit_cost="2.00"),
            _movement("EVT-2", "SKU-0001", "OUTBOUND", date(2026, 6, 5), "3"),
        ]
    )
    formula_ids = {m.formula_id for m in result.metrics}
    assert formula_ids == set(inventory_kpi.FORMULA_IDS)
    for metric in result.metrics:
        assert metric.formula_version == "0.1.0"
        assert metric.sample_count >= 0
        assert metric.name == inventory_kpi.METRIC_NAMES[metric.formula_id]
