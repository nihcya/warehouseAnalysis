"""事件重放内核测试（formula-spec §2：排序、期间归属、冲销、盘点、负库存）。

覆盖口径：

- 期间归属 ``[start_date, end_date)``：历史事件进期初、``move_date == end_date``
  的事件不参与重放；
- 重放顺序 ``(move_date, occurred_at, event_id)`` 升序（含混合时区排序稳定性）；
- 冲销：撤销入库/出库、撤销的撤销复原、期间归属按冲销事件自身时点；
- 盘点：余额替换（差额不计出库量/COGS）、撤销盘点扣减当时差额；
- 调拨：单条记录无方向语义，重放 no-op；
- 负库存：照常计算 + NEGATIVE_BALANCE Warning（fields 含 sku、仓库、日期、数值）；
- 移动加权平均 COGS：多次入库不同成本、退货冲减、无成本记录记 0；
- 仓库范围过滤与直接调用内核时的防御性校验（引用缺失/环）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from contracts import (
    AnalysisRequest,
    EngineDataset,
    EventSource,
    MovementRecord,
    MoveType,
    SkuRecord,
)
from warehouse_engine.errors import DataValidationError
from warehouse_engine.replay import replay_movements, replay_sort_key

REQUEST = AnalysisRequest(
    run_id="run-replay-0001",
    start_date=date(2026, 6, 1),
    end_date=date(2026, 6, 30),
    warehouse_ids=["WH-01"],
)

#: 无仓库限制的请求（验证空 warehouse_ids 视为不限仓库）
UNSCOPED_REQUEST = AnalysisRequest(
    run_id="run-replay-0002",
    start_date=date(2026, 6, 1),
    end_date=date(2026, 6, 30),
    warehouse_ids=[],
)

SKU = SkuRecord(
    sku_id="SKU-0001",
    name="矿泉水 550ml",
    category="饮料",
    unit="瓶",
    unit_cost=Decimal("1.20"),
)


def _movement(
    event_id: str,
    move_type: MoveType | str,
    move_date: date,
    quantity: str | int = "10",
    *,
    sku_id: str = "SKU-0001",
    warehouse_id: str = "WH-01",
    unit_cost: str | None = None,
    occurred_at: datetime | None = None,
    reversal_of: str | None = None,
) -> MovementRecord:
    """构造流水事件（默认 occurred_at 取 move_date 08:00 UTC）。"""
    return MovementRecord(
        event_id=event_id,
        sku_id=sku_id,
        move_type=move_type,
        quantity=Decimal(str(quantity)),
        move_date=move_date,
        occurred_at=occurred_at or datetime(move_date.year, move_date.month, move_date.day, 8, 0, tzinfo=UTC),
        warehouse_id=warehouse_id,
        unit_cost=Decimal(unit_cost) if unit_cost is not None else None,
        source=EventSource.IMPORT,
        reversal_of=reversal_of,
    )


def _replay(
    movements: list[MovementRecord],
    request: AnalysisRequest = REQUEST,
) -> dict[tuple[str, str], Any]:
    """执行重放并按 (sku_id, warehouse_id) 返回桶结果字典。"""
    outcome = replay_movements(request, movements)
    return {bucket.sku_id: bucket for bucket in outcome.buckets}


def _bucket_of(
    movements: list[MovementRecord],
    request: AnalysisRequest = REQUEST,
    sku_id: str = "SKU-0001",
):
    """取单桶结果（断言恰好存在一个桶）。"""
    buckets = _replay(movements, request)
    assert len(buckets) == 1
    return buckets[sku_id]


def test_sort_key_orders_by_date_occurred_at_event_id() -> None:
    """排序键：(move_date, occurred_at, event_id) 升序。"""
    base = datetime(2026, 6, 5, 8, 0, tzinfo=UTC)
    a = _movement("EVT-A", "INBOUND", date(2026, 6, 5), occurred_at=base)
    b = _movement("EVT-B", "INBOUND", date(2026, 6, 5), occurred_at=base + timedelta(hours=1))
    c = _movement("EVT-C", "INBOUND", date(2026, 6, 5), occurred_at=base + timedelta(hours=1))
    assert replay_sort_key(a) < replay_sort_key(b)
    assert replay_sort_key(b) < replay_sort_key(c)


def test_sort_key_normalizes_mixed_timezones() -> None:
    """混合时区 occurred_at 折算 UTC 后排序（+08:00 08:00 == UTC 00:00）。"""
    utc_morning = _movement(
        "EVT-1",
        "INBOUND",
        date(2026, 6, 5),
        occurred_at=datetime(2026, 6, 5, 0, 0, tzinfo=UTC),
    )
    shanghai_later = _movement(
        "EVT-2",
        "INBOUND",
        date(2026, 6, 5),
        occurred_at=datetime(2026, 6, 5, 9, 0, tzinfo=timezone(timedelta(hours=8))),
    )
    # +08:00 09:00 == UTC 01:00，晚于 UTC 00:00
    assert replay_sort_key(utc_morning) < replay_sort_key(shanghai_later)


def test_period_attribution_left_closed_right_open() -> None:
    """start_date 当天计入本期；end_date 当天不参与重放。"""
    bucket = _bucket_of(
        [
            _movement("EVT-H", "INBOUND", date(2026, 5, 20), "7"),  # 历史
            _movement("EVT-S", "INBOUND", date(2026, 6, 1), "3"),  # 期间首日
            _movement("EVT-E", "INBOUND", date(2026, 6, 30), "100"),  # end_date 当天
        ]
    )
    assert bucket.opening_qty == Decimal(7)
    assert bucket.closing_qty == Decimal(10)
    # end_date 当天的事件完全未参与（未触发出库量/COGS 变化）
    assert bucket.period_out_qty == Decimal(0)


def test_history_events_do_not_count_as_period_outflow() -> None:
    """历史出库计入期初余额，不计入本期出库量与 COGS。"""
    bucket = _bucket_of(
        [
            _movement("EVT-1", "INBOUND", date(2026, 5, 10), "10", unit_cost="2.00"),
            _movement("EVT-2", "OUTBOUND", date(2026, 5, 20), "4"),
        ]
    )
    assert bucket.opening_qty == Decimal(6)
    assert bucket.closing_qty == Decimal(6)
    assert bucket.period_out_qty == Decimal(0)
    assert bucket.period_cogs == Decimal(0)


def test_no_period_events_bucket_keeps_opening_as_closing() -> None:
    """仅有历史事件的桶：期初即期末。"""
    bucket = _bucket_of([_movement("EVT-1", "INBOUND", date(2026, 4, 1), "12")])
    assert bucket.opening_qty == Decimal(12)
    assert bucket.closing_qty == Decimal(12)


def test_date_disorder_tolerated_by_replay_order() -> None:
    """日期乱序输入按重放排序规则归位（乱序可容忍，不阻断）。"""
    bucket = _bucket_of(
        [
            _movement("EVT-3", "INBOUND", date(2026, 6, 20), "5"),
            _movement("EVT-1", "INBOUND", date(2026, 6, 2), "10"),
            _movement("EVT-2", "OUTBOUND", date(2026, 6, 10), "3"),
        ]
    )
    assert bucket.opening_qty == Decimal(0)
    assert bucket.closing_qty == Decimal(12)


def test_reversal_undoes_inbound() -> None:
    """冲销入库：期末余额回退到冲销前，等价于撤销原事件。"""
    bucket = _bucket_of(
        [
            _movement("EVT-1", "INBOUND", date(2026, 6, 2), "10"),
            _movement("EVT-R", "REVERSAL", date(2026, 6, 10), "10", reversal_of="EVT-1"),
        ]
    )
    assert bucket.opening_qty == Decimal(0)
    assert bucket.closing_qty == Decimal(0)


def test_reversal_undoes_outbound_and_restores_cogs() -> None:
    """冲销出库：余额复原，期间出库量与 COGS 等额冲回。"""
    bucket = _bucket_of(
        [
            _movement("EVT-1", "INBOUND", date(2026, 6, 2), "10", unit_cost="2.00"),
            _movement("EVT-2", "OUTBOUND", date(2026, 6, 5), "4"),
            _movement("EVT-R", "REVERSAL", date(2026, 6, 10), "4", reversal_of="EVT-2"),
        ]
    )
    assert bucket.closing_qty == Decimal(10)
    assert bucket.period_out_qty == Decimal(0)
    assert bucket.period_cogs == Decimal(0)


def test_reversal_of_reversal_restores_original_event() -> None:
    """撤销的撤销 = 复原原事件（引用链层数取奇偶）。"""
    bucket = _bucket_of(
        [
            _movement("EVT-1", "INBOUND", date(2026, 6, 2), "10"),
            _movement("EVT-R1", "REVERSAL", date(2026, 6, 5), "10", reversal_of="EVT-1"),
            _movement("EVT-R2", "REVERSAL", date(2026, 6, 10), "10", reversal_of="EVT-R1"),
        ]
    )
    assert bucket.closing_qty == Decimal(10)


def test_reversal_period_attribution_at_reversal_time() -> None:
    """冲销发生在期内时按冲销时点生效：期初=历史重放余额，还原计入本期净变动。"""
    # 历史出库 + 期内冲销：期初按历史事件重放（10−3=7），
    # 冲销的还原属于本期净变动（closing=10），期间出库量不含该笔（历史事件从未计入）
    bucket = _bucket_of(
        [
            _movement("EVT-1", "INBOUND", date(2026, 5, 20), "10"),
            _movement("EVT-2", "OUTBOUND", date(2026, 5, 25), "3"),
            _movement("EVT-R", "REVERSAL", date(2026, 6, 10), "3", reversal_of="EVT-2"),
        ]
    )
    assert bucket.opening_qty == Decimal(7)
    assert bucket.closing_qty == Decimal(10)
    assert bucket.period_out_qty == Decimal(0)
    assert bucket.period_cogs == Decimal(0)


def test_reversal_of_out_of_scope_base_is_noop() -> None:
    """基础事件在仓库范围外：冲销无从撤销，不产生范围外桶泄漏。"""
    movements = [
        _movement("EVT-1", "INBOUND", date(2026, 6, 2), "10", warehouse_id="WH-02"),
        _movement("EVT-R", "REVERSAL", date(2026, 6, 5), "10", reversal_of="EVT-1"),
    ]
    outcome = replay_movements(REQUEST, movements)
    # 冲销（WH-01，在范围内）作用于 WH-02 的基础事件：基础事件未参与重放，跳过
    assert outcome.buckets == ()


def test_reversal_of_future_base_is_noop() -> None:
    """基础事件在重放视界外（move_date >= end_date）：冲销不生效。"""
    movements = [
        _movement("EVT-1", "INBOUND", date(2026, 7, 5), "10"),
        _movement("EVT-R", "REVERSAL", date(2026, 6, 5), "10", reversal_of="EVT-1"),
    ]
    outcome = replay_movements(REQUEST, movements)
    assert outcome.buckets == ()


def test_stocktake_replaces_balance_without_outflow() -> None:
    """盘点替换余额：差额不计出库量，也不计 COGS。"""
    bucket = _bucket_of(
        [
            _movement("EVT-1", "INBOUND", date(2026, 6, 2), "10", unit_cost="2.00"),
            _movement("EVT-2", "STOCKTAKE", date(2026, 6, 10), "7"),
        ]
    )
    assert bucket.closing_qty == Decimal(7)
    assert bucket.period_out_qty == Decimal(0)
    assert bucket.period_cogs == Decimal(0)


def test_stocktake_replacement_affects_opening_when_before_period() -> None:
    """历史盘点参与期初重放：期初 = 替换后的余额。"""
    bucket = _bucket_of(
        [
            _movement("EVT-1", "INBOUND", date(2026, 5, 10), "10"),
            _movement("EVT-2", "STOCKTAKE", date(2026, 5, 20), "6"),
        ]
    )
    assert bucket.opening_qty == Decimal(6)
    assert bucket.closing_qty == Decimal(6)


def test_stocktake_undo_subtracts_replacement_delta() -> None:
    """撤销盘点 = 扣除当时的替换差额（与中间事件无关）。"""
    bucket = _bucket_of(
        [
            _movement("EVT-1", "INBOUND", date(2026, 6, 2), "10"),
            _movement("EVT-2", "STOCKTAKE", date(2026, 6, 5), "6"),
            _movement("EVT-3", "OUTBOUND", date(2026, 6, 8), "2"),
            _movement("EVT-R", "REVERSAL", date(2026, 6, 12), "6", reversal_of="EVT-2"),
        ]
    )
    # 撤销差额 = 6 − 10 = −4 → 当前余额 4 − (6−10) = 8（等价于未发生盘点且保留出库）
    assert bucket.closing_qty == Decimal(8)


def test_transfer_is_replay_noop() -> None:
    """调拨（单条记录）不改变余额、出库量与 COGS。"""
    bucket = _bucket_of(
        [
            _movement("EVT-1", "INBOUND", date(2026, 6, 2), "10", unit_cost="2.00"),
            _movement("EVT-2", "TRANSFER", date(2026, 6, 5), "10"),
        ]
    )
    assert bucket.closing_qty == Decimal(10)
    assert bucket.period_out_qty == Decimal(0)
    assert bucket.period_cogs == Decimal(0)


def test_negative_balance_tracked_with_warning_fields() -> None:
    """负库存照常计算，NEGATIVE_BALANCE 定位 sku/仓库/首个负余额日期与数值。"""
    movements = [
        _movement("EVT-1", "OUTBOUND", date(2026, 6, 5), "5"),
        _movement("EVT-2", "INBOUND", date(2026, 6, 10), "8"),
    ]
    outcome = replay_movements(REQUEST, movements)
    assert outcome.buckets[0].closing_qty == Decimal(3)
    assert outcome.buckets[0].first_negative_date == date(2026, 6, 5)
    assert outcome.buckets[0].first_negative_value == Decimal(-5)
    assert outcome.buckets[0].period_negative is True
    assert len(outcome.warnings) == 1
    warning = outcome.warnings[0]
    assert warning.code == "NEGATIVE_BALANCE"
    assert warning.blocking is False
    assert warning.fields == ["SKU-0001", "WH-01", "2026-06-05", "-5"]


def test_opening_negative_marks_period_negative() -> None:
    """期初即为负（历史事件造成）：进入本期时点视为期内负库存。"""
    movements = [
        _movement("EVT-1", "OUTBOUND", date(2026, 5, 20), "3"),
        _movement("EVT-2", "INBOUND", date(2026, 6, 10), "1"),
    ]
    outcome = replay_movements(REQUEST, movements)
    assert outcome.buckets[0].opening_qty == Decimal(-3)
    assert outcome.buckets[0].period_negative is True


def test_moving_weighted_average_cogs() -> None:
    """多次入库不同成本：出库按移动加权平均计 COGS。"""
    movements = [
        _movement("EVT-1", "INBOUND", date(2026, 6, 2), "10", unit_cost="2.00"),
        _movement("EVT-2", "INBOUND", date(2026, 6, 3), "10", unit_cost="4.00"),
        _movement("EVT-3", "OUTBOUND", date(2026, 6, 5), "5"),
    ]
    outcome = replay_movements(REQUEST, movements)
    # avg = (10×2 + 10×4)/20 = 3.00 → COGS = 5 × 3.00 = 15.00
    assert outcome.buckets[0].period_cogs == Decimal("15.00")


def test_return_reduces_cogs_at_current_average() -> None:
    """退货按当前 avg_cost 冲减 COGS 并冲减出库量。"""
    movements = [
        _movement("EVT-1", "INBOUND", date(2026, 6, 2), "10", unit_cost="2.00"),
        _movement("EVT-2", "OUTBOUND", date(2026, 6, 5), "6"),
        _movement("EVT-3", "RETURN", date(2026, 6, 8), "2"),
    ]
    outcome = replay_movements(REQUEST, movements)
    bucket = outcome.buckets[0]
    assert bucket.period_cogs == Decimal("8.00")  # 6×2 − 2×2
    assert bucket.period_out_qty == Decimal(4)  # 6 − 2
    assert bucket.closing_qty == Decimal(6)


def test_outbound_without_known_cost_records_zero_cogs() -> None:
    """出库时无任何已知成本记录：该笔 COGS 记 0 并标记 SKU。"""
    movements = [
        _movement("EVT-1", "INBOUND", date(2026, 6, 2), "10"),  # 无 unit_cost
        _movement("EVT-2", "OUTBOUND", date(2026, 6, 5), "4"),
    ]
    outcome = replay_movements(REQUEST, movements)
    assert outcome.buckets[0].period_cogs == Decimal(0)
    assert "SKU-0001" in outcome.cogs_missing_cost_skus


def test_inbound_zero_denominator_skips_average_update() -> None:
    """balance + in = 0（负库存后等量回补）时不重算 avg_cost（§3.6 边界 3）。"""
    movements = [
        _movement("EVT-1", "INBOUND", date(2026, 6, 2), "10", unit_cost="2.00"),
        _movement("EVT-2", "OUTBOUND", date(2026, 6, 3), "15"),  # 余额 −5
        _movement("EVT-3", "INBOUND", date(2026, 6, 5), "5", unit_cost="3.00"),  # 分母 0 跳过
        _movement("EVT-4", "OUTBOUND", date(2026, 6, 8), "1"),  # 仍按 avg 2.00 计
    ]
    outcome = replay_movements(REQUEST, movements)
    # avg_cost 保持 2.00：COGS = 15×2 + 1×2 = 32.00（若未跳过更新，avg 会改变）
    assert outcome.buckets[0].period_cogs == Decimal("32.00")
    assert outcome.buckets[0].closing_qty == Decimal(-1)


def test_warehouse_scope_filters_buckets() -> None:
    """仓库范围过滤：范围外事件不参与重放；空范围视为不限仓库。"""
    movements = [
        _movement("EVT-1", "INBOUND", date(2026, 6, 2), "10", warehouse_id="WH-01"),
        _movement("EVT-2", "INBOUND", date(2026, 6, 3), "5", warehouse_id="WH-02"),
    ]
    scoped = replay_movements(REQUEST, movements)
    assert [(b.warehouse_id, b.closing_qty) for b in scoped.buckets] == [("WH-01", Decimal(10))]
    unscoped = replay_movements(UNSCOPED_REQUEST, movements)
    assert sorted((b.warehouse_id, b.closing_qty) for b in unscoped.buckets) == [
        ("WH-01", Decimal(10)),
        ("WH-02", Decimal(5)),
    ]


def test_sku_level_aggregation_sums_buckets() -> None:
    """SKU 级 = 各仓分桶之和。"""
    movements = [
        _movement("EVT-1", "INBOUND", date(2026, 6, 2), "10", warehouse_id="WH-01"),
        _movement("EVT-2", "INBOUND", date(2026, 6, 3), "7", warehouse_id="WH-02"),
    ]
    outcome = replay_movements(UNSCOPED_REQUEST, movements)
    by_warehouse = {b.warehouse_id: b for b in outcome.buckets}
    total = sum(bucket.closing_qty for bucket in by_warehouse.values())
    assert total == Decimal(17)


def test_replay_raises_defensively_on_missing_reference() -> None:
    """绕过校验直接调用内核：引用缺失防御性抛 DataValidationError。"""
    movements = [
        _movement("EVT-R", "REVERSAL", date(2026, 6, 5), "10", reversal_of="EVT-NONE"),
    ]
    with pytest.raises(DataValidationError):
        replay_movements(REQUEST, movements)


def test_replay_raises_defensively_on_reference_cycle() -> None:
    """绕过校验直接调用内核：引用链成环防御性抛 DataValidationError。"""
    movements = [
        _movement("EVT-R1", "REVERSAL", date(2026, 6, 5), "10", reversal_of="EVT-R2"),
        _movement("EVT-R2", "REVERSAL", date(2026, 6, 6), "10", reversal_of="EVT-R1"),
    ]
    with pytest.raises(DataValidationError):
        replay_movements(REQUEST, movements)


def test_engine_dataset_replay_integration() -> None:
    """经 EngineDataset 模型解析后重放：结果与直接传事件一致。"""
    payload_movements: list[dict[str, Any]] = [
        {
            "event_id": "EVT-1",
            "sku_id": "SKU-0001",
            "move_type": "INBOUND",
            "quantity": "10",
            "move_date": "2026-06-02",
            "occurred_at": "2026-06-02T08:00:00Z",
            "warehouse_id": "WH-01",
            "unit_cost": "2.00",
            "source": "IMPORT",
        },
        {
            "event_id": "EVT-2",
            "move_type": "OUTBOUND",
            "sku_id": "SKU-0001",
            "quantity": "4",
            "move_date": "2026-06-05",
            "occurred_at": "2026-06-05T08:00:00Z",
            "warehouse_id": "WH-01",
            "source": "IMPORT",
        },
    ]
    dataset = EngineDataset.model_validate(
        {"schema_version": "1.0", "movements": payload_movements}
    )
    outcome = replay_movements(REQUEST, dataset.movements)
    assert outcome.buckets[0].closing_qty == Decimal(6)
    assert outcome.buckets[0].period_cogs == Decimal("8.00")
    assert outcome.buckets[0].opening_qty == Decimal(0)
