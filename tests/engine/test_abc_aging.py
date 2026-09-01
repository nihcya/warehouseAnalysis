"""abc_aging 计算器测试（F-ABC-001 / F-AGE-001 / F-STALE-001，formula-spec §4~§6）。

用例覆盖 formula-spec 各节声明的黄金边界：

- ABC：累计占比恰为 80%/95%、``amt`` 并列、零出库、全零；
- 库龄：30/60/90/180 天档位边界（左闭右开）、批次口径、无批次 FIFO 近似、
  期末 > 0 但全历史无入库事件（``DATE_MISSING``）、期末为 0 不参与；
- 呆滞：``no_outflow_days`` 90 vs 91、``closing_qty`` 阈值边界、新品豁免
  边界、停产排除，以及呆滞金额与呆滞率。

数值均为手工推导，禁止以引擎输出回填。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from contracts import (
    AnalysisRequest,
    EngineDataset,
    EventSource,
    MovementRecord,
    SkuRecord,
)
from warehouse_engine.calculators import abc_aging

START = date(2026, 6, 1)
END = date(2026, 6, 30)
UNIT = "件"


def _request(**parameters: object) -> AnalysisRequest:
    return AnalysisRequest(
        run_id="run-abc-aging",
        start_date=START,
        end_date=END,
        warehouse_ids=["WH-01"],
        parameters=dict(parameters),
    )


def _movement(
    event_id: str,
    sku_id: str,
    move_type: str,
    move_date: date,
    quantity: str,
    unit_cost: str | None = None,
    lot_id: str | None = None,
) -> MovementRecord:
    return MovementRecord(
        event_id=event_id,
        sku_id=sku_id,
        move_type=move_type,
        quantity=Decimal(quantity),
        move_date=move_date,
        occurred_at=datetime(move_date.year, move_date.month, move_date.day, 8, 0, tzinfo=UTC),
        warehouse_id="WH-01",
        unit_cost=None if unit_cost is None else Decimal(unit_cost),
        lot_id=lot_id,
        source=EventSource.IMPORT,
    )


def _flow(specs: list[tuple[str, str, str]], *, out_date: date = date(2026, 6, 10)) -> EngineDataset:
    """由 (sku_id, 入库单位成本, 期间出库量) 构造最小数据集。

    每个 SKU 一条带成本的入库（数量 1000）与一条出库，使 §3.5 单位成本与
    期间出库量可直接由 spec 控制。
    """
    movements: list[MovementRecord] = []
    skus: list[SkuRecord] = []
    for sku_id, unit_cost, out_qty in specs:
        skus.append(
            SkuRecord(sku_id=sku_id, name=sku_id, category="测试", unit=UNIT)
        )
        movements.append(
            _movement(f"IN-{sku_id}", sku_id, "INBOUND", date(2026, 5, 1), "1000", unit_cost)
        )
        if Decimal(out_qty) > 0:
            movements.append(
                _movement(f"OUT-{sku_id}", sku_id, "OUTBOUND", out_date, out_qty)
            )
    return EngineDataset(skus=skus, movements=movements)


# --- F-ABC-001 --------------------------------------------------------------


def test_abc_cumulative_ratio_boundaries_80_and_95() -> None:
    """累计占比恰为 80% 归 A、恰为 95% 归 B，其余归 C（各 SKU 恰归一档）。"""
    # amt：80 / 15 / 5 → 累计 0.80 / 0.95 / 1.00
    dataset = _flow(
        [("SKU-0001", "1.00", "80"), ("SKU-0002", "1.00", "15"), ("SKU-0003", "1.00", "5")]
    )
    result = abc_aging.calculate(_request(), dataset)

    classes = {row.sku_id: row.abc_class for row in result.abc_rows}
    assert classes == {"SKU-0001": "A", "SKU-0002": "B", "SKU-0003": "C"}
    ratios = {row.sku_id: row.cum_ratio for row in result.abc_rows}
    assert ratios["SKU-0001"] == Decimal("0.8")
    assert ratios["SKU-0002"] == Decimal("0.95")
    assert ratios["SKU-0003"] == Decimal(1)


def test_abc_ties_break_by_sku_id_ascending() -> None:
    """amt 并列时按 sku_id 字典序升序（两级稳定排序，同输入必同输出）。"""
    dataset = _flow(
        [("SKU-B", "1.00", "10"), ("SKU-A", "1.00", "10"), ("SKU-C", "1.00", "80")]
    )
    result = abc_aging.calculate(_request(), dataset)

    order = [(row.rank, row.sku_id, row.abc_class) for row in result.abc_rows]
    assert order == [(1, "SKU-C", "A"), (2, "SKU-A", "B"), (3, "SKU-B", "C")]


def test_abc_zero_amt_goes_to_c_and_emits_no_outflow() -> None:
    """amt = 0（期间无出库）一律归 C 并发 NO_OUTFLOW（fields 含 sku_id）。"""
    # amt：70 / 20 / 10 / 0 → 累计 0.70 / 0.90 / 1.00 / 1.00
    # 注意：按 §4 冻结口径 cum 为"含自身"的累计占比，最后一个非零 SKU 的
    # cum 恒为 1.0，故出库金额高度集中时该 SKU 归 C（口径原文如此，不改写）
    dataset = _flow(
        [
            ("SKU-0001", "1.00", "70"),
            ("SKU-0002", "1.00", "10"),
            ("SKU-0003", "1.00", "20"),
            ("SKU-0004", "1.00", "0"),
        ]
    )
    result = abc_aging.calculate(_request(), dataset)

    classes = {row.sku_id: row.abc_class for row in result.abc_rows}
    assert classes == {
        "SKU-0001": "A",
        "SKU-0003": "B",
        "SKU-0002": "C",
        "SKU-0004": "C",
    }
    assert {row.sku_id: row.zero_reason for row in result.abc_rows}["SKU-0004"] == "no_outflow"
    assert [(w.code, w.fields) for w in result.warnings] == [("NO_OUTFLOW", ["SKU-0004"])]


def test_abc_missing_unit_cost_treated_as_zero_amt() -> None:
    """单位成本缺失时 amt 记 0 并归 C（不伪造金额口径排序）。"""
    movements = [
        _movement("IN-1", "SKU-0001", "INBOUND", date(2026, 5, 1), "1000", "1.00"),
        _movement("OUT-1", "SKU-0001", "OUTBOUND", date(2026, 6, 10), "50"),
        _movement("IN-2", "SKU-0002", "INBOUND", date(2026, 5, 1), "1000"),  # 无成本
        _movement("OUT-2", "SKU-0002", "OUTBOUND", date(2026, 6, 10), "50"),
    ]
    dataset = EngineDataset(
        skus=[
            SkuRecord(sku_id=sku, name=sku, category="测试", unit=UNIT)
            for sku in ("SKU-0001", "SKU-0002")
        ],
        movements=movements,
    )
    result = abc_aging.calculate(_request(), dataset)

    rows = {row.sku_id: row for row in result.abc_rows}
    assert rows["SKU-0002"].amt == Decimal(0)
    assert rows["SKU-0002"].abc_class == "C"
    assert rows["SKU-0002"].zero_reason == "unit_cost_missing"


def test_abc_all_zero_total_flags_zero_total_amount() -> None:
    """Σ amt = 0（全部 SKU 无出库）：全归 C 且每个 SKU 各一条 NO_OUTFLOW。"""
    dataset = _flow([("SKU-0001", "1.00", "0"), ("SKU-0002", "1.00", "0")])
    result = abc_aging.calculate(_request(), dataset)

    assert all(row.abc_class == "C" for row in result.abc_rows)
    assert [w.fields for w in result.warnings if w.code == "NO_OUTFLOW"] == [
        ["SKU-0001"],
        ["SKU-0002"],
    ]
    metric = next(m for m in result.metrics if m.formula_id == "F-ABC-001")
    assert metric.reason == "zero_total_amount"


# --- F-AGE-001 --------------------------------------------------------------


def _aging_dataset(days: list[int]) -> EngineDataset:
    """为每个库龄天数构造一个 SKU：入库日期 = 观察点 − days。"""
    movements: list[MovementRecord] = []
    skus: list[SkuRecord] = []
    for index, age in enumerate(days, start=1):
        sku_id = f"SKU-{index:04d}"
        inbound_date = END - timedelta(days=age)
        skus.append(SkuRecord(sku_id=sku_id, name=sku_id, category="测试", unit=UNIT))
        movements.append(
            _movement(f"IN-{sku_id}", sku_id, "INBOUND", inbound_date, "100", "2.00")
        )
    return EngineDataset(skus=skus, movements=movements)


def test_aging_bucket_boundaries_are_left_closed_right_open() -> None:
    """恰为 30/60/90/180 天分别归入 [30,60)/[60,90)/[90,180)/[180,+∞)。"""
    dataset = _aging_dataset([29, 30, 60, 90, 180])
    result = abc_aging.calculate(_request(), dataset)

    buckets = {row.sku_id: row.bucket for row in result.aging_rows}
    assert buckets == {
        "SKU-0001": "0-30",  # 29 天
        "SKU-0002": "30-60",  # 恰为 30
        "SKU-0003": "60-90",  # 恰为 60
        "SKU-0004": "90-180",  # 恰为 90
        "SKU-0005": "180+",  # 恰为 180
    }


def test_aging_bucket_quantity_and_amount_distribution() -> None:
    """各档位数量与金额分布（金额按 §3.5 单位成本 2.00 计算）。"""
    dataset = _aging_dataset([29, 30])
    result = abc_aging.calculate(_request(), dataset)

    assert result.bucket_qty["0-30"] == Decimal(100)
    assert result.bucket_qty["30-60"] == Decimal(100)
    assert result.bucket_amount["0-30"] == Decimal("200.00")
    assert result.bucket_amount["30-60"] == Decimal("200.00")


def test_aging_lot_basis_uses_last_inbound_date_of_the_lot() -> None:
    """有 lot_id 时按批次分桶，库龄 = 观察点 − 该批次最近一次入库日期。"""
    movements = [
        _movement(
            "IN-1", "SKU-0001", "INBOUND", END - timedelta(days=50), "100", "2.00", lot_id="L1"
        ),
        _movement(
            "IN-2", "SKU-0001", "INBOUND", END - timedelta(days=10), "50", "2.00", lot_id="L1"
        ),
    ]
    dataset = EngineDataset(
        skus=[SkuRecord(sku_id="SKU-0001", name="SKU-0001", category="测试", unit=UNIT)],
        movements=movements,
    )
    result = abc_aging.calculate(_request(), dataset)

    assert len(result.aging_rows) == 1
    row = result.aging_rows[0]
    assert row.age_basis == "lot"
    assert row.lot_id == "L1"
    assert row.age_days == Decimal(10)  # 取最近一次入库
    assert row.bucket == "0-30"


def test_aging_no_lot_uses_fifo_residual_weighted_average() -> None:
    """无批次时按构成期末余额的入库事件加权平均，标注 age_basis=constituent。"""
    movements = [
        _movement("IN-1", "SKU-0001", "INBOUND", END - timedelta(days=60), "100", "2.00"),
        _movement("IN-2", "SKU-0001", "INBOUND", END - timedelta(days=20), "100", "2.00"),
        _movement("OUT-1", "SKU-0001", "OUTBOUND", date(2026, 6, 10), "100"),
    ]
    dataset = EngineDataset(
        skus=[SkuRecord(sku_id="SKU-0001", name="SKU-0001", category="测试", unit=UNIT)],
        movements=movements,
    )
    result = abc_aging.calculate(_request(), dataset)

    # FIFO 消耗掉最早的 100（60 天），剩余 100（20 天）构成期末余额
    assert len(result.aging_rows) == 1
    row = result.aging_rows[0]
    assert row.age_basis == "constituent"
    assert row.age_days == Decimal(20)


def test_aging_falls_back_to_all_history_when_residual_insufficient() -> None:
    """盘点替换余额后残余构成不足，回退全历史入库（age_basis=all_history）。"""
    movements = [
        _movement("IN-1", "SKU-0001", "INBOUND", END - timedelta(days=60), "100", "2.00"),
        _movement("IN-2", "SKU-0001", "INBOUND", END - timedelta(days=20), "100", "2.00"),
        _movement("ST-1", "SKU-0001", "STOCKTAKE", date(2026, 6, 15), "150"),
    ]
    dataset = EngineDataset(
        skus=[SkuRecord(sku_id="SKU-0001", name="SKU-0001", category="测试", unit=UNIT)],
        movements=movements,
    )
    result = abc_aging.calculate(_request(), dataset)

    assert len(result.aging_rows) == 1
    row = result.aging_rows[0]
    assert row.age_basis == "all_history"
    # 全历史加权平均：(100×60 + 100×20) / 200 = 40
    assert row.age_days == Decimal(40)


def test_aging_no_inbound_event_emits_date_missing() -> None:
    """期末 > 0 但全历史无入库事件 → 跳过并发 DATE_MISSING(no_inbound_event)。"""
    movements = [
        _movement("ST-1", "SKU-0001", "STOCKTAKE", date(2026, 6, 15), "50"),
    ]
    dataset = EngineDataset(
        skus=[SkuRecord(sku_id="SKU-0001", name="SKU-0001", category="测试", unit=UNIT)],
        movements=movements,
    )
    result = abc_aging.calculate(_request(), dataset)

    assert result.aging_rows == ()
    # 该 SKU 期间无出库（amt=0 → NO_OUTFLOW）且无入库事件（→ DATE_MISSING），
    # Warning 顺序固定为 NO_OUTFLOW 先于 DATE_MISSING
    assert [(w.code, w.fields) for w in result.warnings] == [
        ("NO_OUTFLOW", ["SKU-0001"]),
        ("DATE_MISSING", ["SKU-0001", "no_inbound_event"]),
    ]


def test_aging_excludes_skus_without_closing_stock() -> None:
    """期末库存为 0 的 SKU 不参与库龄分布（§5 边界 2）。"""
    dataset = _aging_dataset([30])
    extra = _movement("OUT-X", "SKU-0001", "OUTBOUND", date(2026, 6, 20), "100")
    dataset = EngineDataset(
        skus=dataset.skus,
        movements=[*dataset.movements, extra],
    )
    result = abc_aging.calculate(_request(), dataset)

    assert result.aging_rows == ()


def test_aging_metric_is_null_without_closing_stock() -> None:
    """无参与桶时平均库龄输出 "null" 并标注 reason=no_closing_stock。"""
    dataset = _aging_dataset([30])
    dataset = EngineDataset(
        skus=dataset.skus,
        movements=[*dataset.movements, _movement("OUT-X", "SKU-0001", "OUTBOUND", date(2026, 6, 20), "100")],
    )
    result = abc_aging.calculate(_request(), dataset)

    metric = next(m for m in result.metrics if m.formula_id == "F-AGE-001")
    assert metric.value == "null"
    assert metric.reason == "no_closing_stock"
    assert metric.sample_count == 0


# --- F-STALE-001 ------------------------------------------------------------


def _stale_dataset(last_outbound: date, *, inbound: date = date(2026, 1, 5)) -> EngineDataset:
    """构造末次出库日期可控的数据集（入库 1000，出库 10，期末 990）。"""
    return EngineDataset(
        skus=[SkuRecord(sku_id="SKU-0001", name="SKU-0001", category="测试", unit=UNIT)],
        movements=[
            _movement("IN-1", "SKU-0001", "INBOUND", inbound, "1000", "2.00"),
            _movement("OUT-1", "SKU-0001", "OUTBOUND", last_outbound, "10"),
        ],
    )


def test_stale_90_days_not_stale_91_days_stale() -> None:
    """no_outflow_days 90 非呆滞、91 呆滞（严格大于 stale_window_days）。"""
    not_stale = abc_aging.calculate(
        _request(), _stale_dataset(date(2026, 4, 1))
    ).stale_rows[0]
    assert not_stale.no_outflow_days == 90
    assert not_stale.is_stale is False
    assert not_stale.excluded_reason == "within_window"

    stale = abc_aging.calculate(_request(), _stale_dataset(date(2026, 3, 31))).stale_rows[0]
    assert stale.no_outflow_days == 91
    assert stale.is_stale is True
    assert stale.excluded_reason is None


def test_stale_min_stock_qty_boundary() -> None:
    """closing_qty 恰等于 min_stock_qty（默认 1）时不判呆滞（严格大于）。"""
    dataset = EngineDataset(
        skus=[SkuRecord(sku_id="SKU-0001", name="SKU-0001", category="测试", unit=UNIT)],
        movements=[
            _movement("IN-1", "SKU-0001", "INBOUND", date(2026, 1, 5), "1", "2.00"),
            _movement("OUT-1", "SKU-0001", "OUTBOUND", date(2026, 3, 31), "0.5"),
        ],
    )
    result = abc_aging.calculate(_request(), dataset)
    row = result.stale_rows[0]

    assert row.closing_qty == Decimal("0.5")
    assert row.is_stale is False
    assert row.excluded_reason == "below_min_stock"


def test_stale_new_inbound_grace_boundary() -> None:
    """新品豁免：首次入库晚于（窗口起点 − 30 天）时不判呆滞。"""
    # 观察窗口起点 = END − 90 = 2026-04-01；豁免分界 = 2026-03-02
    excluded = abc_aging.calculate(
        _request(), _stale_dataset(date(2026, 3, 10), inbound=date(2026, 3, 3))
    ).stale_rows[0]
    assert excluded.excluded_reason == "new_inbound"
    assert excluded.is_stale is False

    not_excluded = abc_aging.calculate(
        _request(), _stale_dataset(date(2026, 3, 10), inbound=date(2026, 3, 2))
    ).stale_rows[0]
    assert not_excluded.excluded_reason != "new_inbound"


def test_stale_discontinued_skus_excluded() -> None:
    """停产标记 SKU 不判呆滞，标注 excluded_reason=discontinued。"""
    dataset = _stale_dataset(date(2026, 3, 31))
    result = abc_aging.calculate(_request(discontinued_skus=["SKU-0001"]), dataset)

    row = result.stale_rows[0]
    assert row.is_stale is False
    assert row.excluded_reason == "discontinued"
    # 被排除本身不发 Warning；数据集期间无出库故另有 ABC 的 NO_OUTFLOW
    assert [w.code for w in result.warnings] == ["NO_OUTFLOW"]


def test_stale_amount_and_ratio() -> None:
    """呆滞金额 = 呆滞 SKU 的 closing_qty × §3.5 单位成本；呆滞率 = 金额/总价值。"""
    dataset = EngineDataset(
        skus=[
            SkuRecord(sku_id=sku, name=sku, category="测试", unit=UNIT)
            for sku in ("SKU-0001", "SKU-0002")
        ],
        movements=[
            _movement("IN-1", "SKU-0001", "INBOUND", date(2026, 1, 5), "1000", "2.00"),
            _movement("OUT-1", "SKU-0001", "OUTBOUND", date(2026, 3, 31), "10"),
            _movement("IN-2", "SKU-0002", "INBOUND", date(2026, 1, 5), "1000", "2.00"),
            _movement("OUT-2", "SKU-0002", "OUTBOUND", date(2026, 6, 20), "10"),
        ],
    )
    result = abc_aging.calculate(_request(), dataset)

    stale_skus = {row.sku_id for row in result.stale_rows if row.is_stale}
    assert stale_skus == {"SKU-0001"}  # SKU-0002 末次出库在窗口内
    # 呆滞数量 990、金额 990 × 2.00 = 1980.00
    assert result.stale_qty == Decimal(990)
    metric = next(m for m in result.metrics if m.formula_id == "F-STALE-001")
    assert metric.value == "1980.00"
    assert metric.unit == "CNY"
    assert metric.sample_count == 1
    # 总价值 = 990×2 + 990×2 = 3960 → 呆滞率 0.5
    assert result.stale_ratio == Decimal("0.5")


def test_stale_no_outflow_record_reason() -> None:
    """既无出库也无入库记录时 no_outflow_days 为 None，标注 no_outflow_record。"""
    dataset = EngineDataset(
        skus=[SkuRecord(sku_id="SKU-0001", name="SKU-0001", category="测试", unit=UNIT)],
        movements=[_movement("ST-1", "SKU-0001", "STOCKTAKE", date(2026, 6, 15), "50")],
    )
    result = abc_aging.calculate(_request(), dataset)

    row = result.stale_rows[0]
    assert row.no_outflow_days is None
    assert row.excluded_reason == "no_outflow_record"


# --- 指标契约与边界 ---------------------------------------------------------


def test_metric_ids_units_and_formula_version() -> None:
    """三个指标携带冻结的 formula_id / formula_version / unit 与指标名。"""
    dataset = _flow([("SKU-0001", "1.00", "10")])
    result = abc_aging.calculate(_request(), dataset)

    assert [m.formula_id for m in result.metrics] == list(abc_aging.FORMULA_IDS)
    assert all(m.formula_version == "0.1.0" for m in result.metrics)
    assert [m.name for m in result.metrics] == [
        "ABC.CLASSIFIED_SKU_COUNT",
        "AGING.AVG_AGE_DAYS",
        "STALE.AMOUNT",
    ]
    assert [m.unit for m in result.metrics] == ["SKU", "天", "CNY"]


def test_custom_parameters_are_honoured() -> None:
    """ABC 阈值、库龄分桶边界与呆滞窗口均可通过 parameters 覆盖。"""
    dataset = _flow([("SKU-0001", "1.00", "60"), ("SKU-0002", "1.00", "40")])
    result = abc_aging.calculate(
        _request(abc_threshold_a="0.50", abc_threshold_b="0.90", stale_window_days=5),
        dataset,
    )

    classes = {row.sku_id: row.abc_class for row in result.abc_rows}
    # 60 → cum 0.60 > 0.50 且 ≤ 0.90 → B；40 → cum 1.00 → C
    assert classes == {"SKU-0001": "B", "SKU-0002": "C"}


def test_empty_dataset_returns_null_degraded_metrics() -> None:
    """空数据集：ABC 计数 0、平均库龄 null、呆滞金额 0.00，不发 Warning。"""
    result = abc_aging.calculate(_request(), EngineDataset())

    by_id = {m.formula_id: m for m in result.metrics}
    # 契约 ResultMetric.value 统一以字符串传输（Decimal 语义），空集为 "0"
    assert by_id["F-ABC-001"].value == "0"
    assert by_id["F-ABC-001"].sample_count == 0
    assert by_id["F-AGE-001"].value == "null"
    assert by_id["F-STALE-001"].value == "0.00"
    assert result.warnings == ()
    assert result.stale_ratio is None
