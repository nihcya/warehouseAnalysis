"""重放内核 M2 采集项测试（daily_out_by_sku、出库/入库日期索引、批次与残余入库）。

M2 的补货（σ_d）、预测（周需求序列）、库龄（批次/平均）与呆滞（无出库天数）
共用 M1 重放内核新增的采集项，本文件先于实现冻结这些语义：

- ``daily_out_by_sku``：``[start_date, end_date)`` 内逐日需求（当日出库 +
  报废 − 退货，按 §7.1 逐日取 ``max(0, ·)``），零出库日补 0，日期升序；
- ``last_outbound_date_by_sku`` / ``first_inbound_date_by_sku``：全重放视界
  （含 ``move_date < start_date`` 的历史事件）内的末次出库与首次入库日期；
- ``lot_balances``：有 ``lot_id`` 时的期末批次余额（FIFO 消耗后）；
- ``residual_inbound_by_sku``：无 ``lot_id`` 时 FIFO 消耗后构成期末余额的
  入库切片；``all_inbound_by_sku`` 为构成不足时的回退总体（O(1) 内存聚合）。
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
)
from warehouse_engine.replay import replay_movements

START = date(2026, 6, 1)
END = date(2026, 6, 30)


def _request(start: date = START, end: date = END) -> AnalysisRequest:
    return AnalysisRequest(
        run_id="run-replay-m2",
        start_date=start,
        end_date=end,
        warehouse_ids=["WH-01"],
    )


def _movement(
    event_id: str,
    move_type: str,
    move_date: date,
    *,
    sku_id: str = "SKU-0001",
    quantity: str = "10",
    lot_id: str | None = None,
    unit_cost: str | None = "2.00",
    warehouse_id: str = "WH-01",
) -> MovementRecord:
    return MovementRecord(
        event_id=event_id,
        sku_id=sku_id,
        move_type=move_type,
        quantity=Decimal(quantity),
        move_date=move_date,
        occurred_at=datetime(move_date.year, move_date.month, move_date.day, 9, 0, tzinfo=UTC),
        warehouse_id=warehouse_id,
        unit_cost=None if unit_cost is None else Decimal(unit_cost),
        lot_id=lot_id,
        source=EventSource.IMPORT,
    )


def _dataset(*movements: MovementRecord) -> EngineDataset:
    sku_ids = sorted({movement.sku_id for movement in movements})
    return EngineDataset(
        skus=[
            SkuRecord(sku_id=sku_id, name=sku_id, category="测试", unit="件")
            for sku_id in sku_ids
        ],
        movements=list(movements),
    )


# --- daily_out_by_sku -------------------------------------------------------


def test_daily_out_covers_every_period_day_with_zero_fill() -> None:
    """期间内每一天都有一项，零出库日补 Decimal(0)，按日期升序。"""
    outcome = replay_movements(
        _request(),
        _dataset(_movement("E1", "OUTBOUND", date(2026, 6, 3), quantity="5")).movements,
    )

    series = outcome.daily_out_by_sku["SKU-0001"]
    days = [day for day, _ in series]
    assert days[0] == START
    assert days[-1] == date(2026, 6, 29)  # end_date 当天不计入（左闭右开）
    assert days == sorted(days)
    assert len(series) == 29
    assert dict(series)[date(2026, 6, 3)] == Decimal(5)
    assert dict(series)[date(2026, 6, 4)] == Decimal(0)


def test_daily_out_nets_returns_and_clamps_at_zero() -> None:
    """当日"出库 + 报废 − 退货"为负时逐日取 max(0, ·)（§7.1 日需求口径）。"""
    outcome = replay_movements(
        _request(),
        _dataset(
            _movement("E1", "OUTBOUND", date(2026, 6, 3), quantity="5"),
            _movement("E2", "RETURN", date(2026, 6, 3), quantity="8"),
        ).movements,
    )

    assert dict(outcome.daily_out_by_sku["SKU-0001"])[date(2026, 6, 3)] == Decimal(0)


def test_daily_out_aggregates_scrap_and_ignores_transfer() -> None:
    """报废计入日需求，调拨为 no-op 不计入。"""
    outcome = replay_movements(
        _request(),
        _dataset(
            _movement("E1", "OUTBOUND", date(2026, 6, 3), quantity="5"),
            _movement("E2", "SCRAP", date(2026, 6, 3), quantity="2"),
            _movement("E3", "TRANSFER", date(2026, 6, 3), quantity="100"),
        ).movements,
    )

    assert dict(outcome.daily_out_by_sku["SKU-0001"])[date(2026, 6, 3)] == Decimal(7)


def test_daily_out_sums_across_warehouses_for_same_sku() -> None:
    """SKU 级日需求 = 各仓库分桶之和（与 F-KPI 口径一致）。"""
    request = AnalysisRequest(
        run_id="run-multi-wh",
        start_date=START,
        end_date=END,
        warehouse_ids=["WH-01", "WH-02"],
    )
    outcome = replay_movements(
        request,
        _dataset(
            _movement("E1", "OUTBOUND", date(2026, 6, 3), quantity="5", warehouse_id="WH-01"),
            _movement("E2", "OUTBOUND", date(2026, 6, 3), quantity="3", warehouse_id="WH-02"),
        ).movements,
    )

    assert dict(outcome.daily_out_by_sku["SKU-0001"])[date(2026, 6, 3)] == Decimal(8)


def test_daily_out_excludes_days_outside_period() -> None:
    """期末当天与期前事件不进入日需求序列。"""
    outcome = replay_movements(
        _request(start=date(2026, 6, 10), end=date(2026, 6, 13)),
        _dataset(
            _movement("E1", "OUTBOUND", date(2026, 6, 1), quantity="5"),  # 历史
            _movement("E2", "OUTBOUND", date(2026, 6, 11), quantity="4"),  # 本期
            _movement("E3", "OUTBOUND", date(2026, 6, 13), quantity="9"),  # 期末当天
        ).movements,
    )

    series = dict(outcome.daily_out_by_sku["SKU-0001"])
    assert series == {
        date(2026, 6, 10): Decimal(0),
        date(2026, 6, 11): Decimal(4),
        date(2026, 6, 12): Decimal(0),
    }


# --- 出库 / 入库日期索引 -----------------------------------------------------


def test_last_outbound_and_first_inbound_span_full_replay_view() -> None:
    """末次出库与首次入库日期覆盖全重放视界（含 move_date < start_date 的历史）。"""
    outcome = replay_movements(
        _request(start=date(2026, 7, 1), end=date(2026, 7, 31)),
        _dataset(
            _movement("E1", "INBOUND", date(2026, 5, 10), quantity="10"),
            _movement("E2", "OUTBOUND", date(2026, 5, 20), quantity="2"),
        ).movements,
    )

    assert outcome.first_inbound_date_by_sku["SKU-0001"] == date(2026, 5, 10)
    assert outcome.last_outbound_date_by_sku["SKU-0001"] == date(2026, 5, 20)


def test_last_outbound_takes_latest_across_warehouses() -> None:
    """SKU 级末次出库日期取各仓库最晚者。"""
    request = AnalysisRequest(
        run_id="run-multi-wh", start_date=START, end_date=END, warehouse_ids=["WH-01", "WH-02"]
    )
    outcome = replay_movements(
        request,
        _dataset(
            _movement(
                "E1", "OUTBOUND", date(2026, 6, 5), quantity="1", warehouse_id="WH-01"
            ),
            _movement(
                "E2", "OUTBOUND", date(2026, 6, 9), quantity="1", warehouse_id="WH-02"
            ),
        ).movements,
    )

    assert outcome.last_outbound_date_by_sku["SKU-0001"] == date(2026, 6, 9)


def test_reversal_does_not_count_as_outbound_or_inbound_activity() -> None:
    """冲销事件本身不更新末次出库/首次入库日期（撤销并非出入库行为）。"""
    reversal = _movement("E2", "REVERSAL", date(2026, 6, 20), quantity="4").model_copy(
        update={"reversal_of": "E1"}
    )
    outcome = replay_movements(
        _request(),
        [_movement("E1", "OUTBOUND", date(2026, 6, 5), quantity="4"), reversal],
    )

    assert outcome.last_outbound_date_by_sku["SKU-0001"] == date(2026, 6, 5)
    # 本数据集无任何入库类事件，首次入库索引中不含该 SKU
    assert "SKU-0001" not in outcome.first_inbound_date_by_sku


# --- 批次余额（lot_id）------------------------------------------------------


def test_lot_balances_track_lot_level_closing_after_fifo_consumption() -> None:
    """有 lot_id 时按 (sku, wh, lot) 分桶，出库按 FIFO 从最早批次消耗。"""
    outcome = replay_movements(
        _request(),
        [
            _movement("E1", "INBOUND", date(2026, 6, 1), quantity="10", lot_id="L1"),
            _movement("E2", "INBOUND", date(2026, 6, 5), quantity="10", lot_id="L2"),
            _movement("E3", "OUTBOUND", date(2026, 6, 10), quantity="12"),
        ],
    )

    lots = {(lot.lot_id, str(lot.qty), lot.last_inbound_date) for lot in outcome.lot_balances}
    assert lots == {
        ("L1", "0", date(2026, 6, 1)),  # 10 全部被 FIFO 消耗
        ("L2", "8", date(2026, 6, 5)),  # 消耗 2 后剩 8
    }


def test_lot_last_inbound_date_updates_on_replenishment() -> None:
    """同一批次再次入库时 last_inbound_date 取最近一次入库日期（§5 批次口径）。"""
    outcome = replay_movements(
        _request(),
        [
            _movement("E1", "INBOUND", date(2026, 6, 1), quantity="10", lot_id="L1"),
            _movement("E2", "INBOUND", date(2026, 6, 8), quantity="5", lot_id="L1"),
        ],
    )

    assert len(outcome.lot_balances) == 1
    lot = outcome.lot_balances[0]
    assert lot.lot_id == "L1"
    assert lot.qty == Decimal(15)
    assert lot.last_inbound_date == date(2026, 6, 8)


def test_lot_balances_sorted_deterministically() -> None:
    """批次余额按 (sku_id, warehouse_id, lot_id) 字典序输出（确定性）。"""
    outcome = replay_movements(
        _request(),
        [
            _movement(
                "E1", "INBOUND", date(2026, 6, 1), quantity="3", lot_id="L2", sku_id="SKU-B"
            ),
            _movement(
                "E2", "INBOUND", date(2026, 6, 1), quantity="3", lot_id="L1", sku_id="SKU-A"
            ),
        ],
    )

    keys = [(lot.sku_id, lot.warehouse_id, lot.lot_id) for lot in outcome.lot_balances]
    assert keys == sorted(keys)


# --- 残余入库与全历史回退 ----------------------------------------------------


def test_residual_inbound_keeps_only_unconsumed_quantity() -> None:
    """无 lot_id 时 FIFO 消耗后仅保留构成期末余额的入库切片。"""
    outcome = replay_movements(
        _request(),
        [
            _movement("E1", "INBOUND", date(2026, 6, 1), quantity="10", lot_id=None),
            _movement("E2", "INBOUND", date(2026, 6, 5), quantity="10", lot_id=None),
            _movement("E3", "OUTBOUND", date(2026, 6, 10), quantity="12"),
        ],
    )

    residual = outcome.residual_inbound_by_sku["SKU-0001"]
    assert [(str(lot.qty), lot.move_date) for lot in residual] == [
        ("8", date(2026, 6, 5))
    ]


def test_all_inbound_aggregate_covers_full_history() -> None:
    """全历史入库聚合覆盖期初前的历史事件，供构成不足时回退。"""
    outcome = replay_movements(
        _request(start=date(2026, 7, 1), end=date(2026, 7, 31)),
        [
            _movement("E1", "INBOUND", date(2026, 5, 1), quantity="10", lot_id=None),
            _movement("E2", "INBOUND", date(2026, 6, 1), quantity="5", lot_id=None),
        ],
    )

    aggregate = outcome.all_inbound_by_sku["SKU-0001"]
    assert aggregate.total_qty == Decimal(15)
    assert aggregate.event_count == 2
    # Σ(qty × ordinal) 可由聚合换算平均库龄（O(1) 内存，避免保存全量事件）
    expected_ordinal = Decimal(10) * Decimal(date(2026, 5, 1).toordinal()) + Decimal(
        5
    ) * Decimal(date(2026, 6, 1).toordinal())
    assert aggregate.weighted_ordinal == expected_ordinal


def test_stocktake_resets_lot_ledger_making_basis_unreliable() -> None:
    """盘点替换余额后批次账不可信：清空切片，由上层回退全历史口径。"""
    outcome = replay_movements(
        _request(),
        [
            _movement("E1", "INBOUND", date(2026, 6, 1), quantity="10", lot_id=None),
            _movement("E2", "STOCKTAKE", date(2026, 6, 15), quantity="7"),
        ],
    )

    # 盘点后残余切片被清空（Σ残余 ≠ 期末余额），上层据此判定"构成不足"
    assert outcome.residual_inbound_by_sku["SKU-0001"] == ()
    assert outcome.all_inbound_by_sku["SKU-0001"].total_qty == Decimal(10)


# --- 向后兼容 ---------------------------------------------------------------


def test_m1_fields_unchanged() -> None:
    """M1 既有字段（buckets / last_cost_by_sku / warnings）不受 M2 扩展影响。"""
    movements = [
        _movement("E1", "INBOUND", date(2026, 6, 1), quantity="10", unit_cost="3.00"),
        _movement("E2", "OUTBOUND", date(2026, 6, 10), quantity="4"),
    ]
    outcome = replay_movements(_request(), movements)

    assert len(outcome.buckets) == 1
    bucket = outcome.buckets[0]
    assert bucket.sku_id == "SKU-0001"
    assert bucket.opening_qty == Decimal(0)
    assert bucket.closing_qty == Decimal(6)
    assert bucket.period_out_qty == Decimal(4)
    assert outcome.last_cost_by_sku["SKU-0001"] == Decimal("3.00")
    assert outcome.warnings == ()


def test_empty_dataset_yields_empty_collections() -> None:
    """空数据集：全部 M2 采集项为空，不产生异常。"""
    outcome = replay_movements(_request(), [])

    assert outcome.daily_out_by_sku == {}
    assert outcome.last_outbound_date_by_sku == {}
    assert outcome.first_inbound_date_by_sku == {}
    assert outcome.lot_balances == ()
    assert outcome.residual_inbound_by_sku == {}
    assert outcome.all_inbound_by_sku == {}
