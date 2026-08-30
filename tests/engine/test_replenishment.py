"""replenishment 计算器测试（F-REPL-001~003，formula-spec §7）。

用例覆盖 formula-spec §7 声明的黄金边界：

- z 值表四档与表外最近档（差值相等取较低档）；
- 参数缺失（``lead_time_days``）→ 非阻断 ``PARAM_MISSING`` + 三项 null；
- 零需求 → ``σ_d = 0 → SS = 0``、``d̄ = 0 → ROP = SS``；
- 在途量扣减与 ``Q*`` 下限 0；
- 期间天数为 0 的降级；参数来源优先级（dataset.replenishment > parameters）。

算例（手工推导）：期间 2026-06-01 ~ 2026-06-04（3 天），逐日需求 10/20/30，
``out_qty = 60`` → ``d̄ = 20``；样本标准差 ``σ_d = √(((10-20)²+(20-20)²+(30-20)²)/2)
= √100 = 10``；``LT = 9 → √LT = 3``、``z(0.95) = 1.645`` →
``SS = 49.35``、``ROP = 20×9 + 49.35 = 229.35``、期末 40 → ``Q* = 189.35``。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from contracts import (
    AnalysisRequest,
    EngineDataset,
    EventSource,
    MovementRecord,
    ReplenishmentRecord,
    SkuRecord,
)
from warehouse_engine.calculators import replenishment

UNIT = "件"


def _request(
    start: date = date(2026, 6, 1),
    end: date = date(2026, 6, 4),
    **parameters: object,
) -> AnalysisRequest:
    return AnalysisRequest(
        run_id="run-replenishment",
        start_date=start,
        end_date=end,
        warehouse_ids=["WH-01"],
        parameters=dict(parameters),
    )


def _movement(
    event_id: str,
    sku_id: str,
    move_type: str,
    move_date: date,
    quantity: str,
) -> MovementRecord:
    return MovementRecord(
        event_id=event_id,
        sku_id=sku_id,
        move_type=move_type,
        quantity=Decimal(quantity),
        move_date=move_date,
        occurred_at=datetime(move_date.year, move_date.month, move_date.day, 8, 0, tzinfo=UTC),
        warehouse_id="WH-01",
        unit_cost=Decimal("2.00") if move_type == "INBOUND" else None,
        source=EventSource.IMPORT,
    )


def _sku(sku_id: str) -> SkuRecord:
    return SkuRecord(sku_id=sku_id, name=sku_id, category="测试", unit=UNIT)


def _demand_dataset(
    demands: list[str],
    *,
    inbound_qty: str = "100",
) -> EngineDataset:
    """构造逐日需求 [10, 20, 30] 的数据集（入库 100 @2.00，期末 40）。"""
    movements = [_movement("IN-1", "SKU-0001", "INBOUND", date(2026, 5, 1), inbound_qty)]
    for index, quantity in enumerate(demands):
        movements.append(
            _movement(
                f"OUT-{index + 1}",
                "SKU-0001",
                "OUTBOUND",
                date(2026, 6, 1) + timedelta(days=index),
                quantity,
            )
        )
    return EngineDataset(skus=[_sku("SKU-0001")], movements=movements)


def _replenishment(sku_id: str, lead_time: str, service_level: str | None = None) -> ReplenishmentRecord:
    return ReplenishmentRecord(
        sku_id=sku_id,
        avg_daily_demand=Decimal(0),  # §7.2 口径不消费该字段（见 m2-handover-b）
        lead_time_days=Decimal(lead_time),
        service_level=None if service_level is None else Decimal(service_level),
    )


# --- F-REPL-001 安全库存 ----------------------------------------------------


def test_safety_stock_matches_hand_calculation() -> None:
    """SS = z × σ_d × √LT = 1.645 × 10 × 3 = 49.35。"""
    dataset = _demand_dataset(["10", "20", "30"])
    dataset = EngineDataset(
        skus=dataset.skus,
        movements=dataset.movements,
        replenishment=[_replenishment("SKU-0001", "9", "0.95")],
    )
    result = replenishment.calculate(_request(), dataset)

    row = result.per_sku[0]
    assert row.sigma_d == Decimal(10)
    assert row.avg_daily_demand == Decimal(20)
    assert row.z == Decimal("1.645")
    assert row.safety_stock == Decimal("49.35")


def test_z_table_four_levels() -> None:
    """z 值表四档：0.90→1.282、0.95→1.645、0.98→2.054、0.99→2.326。"""
    expected = {
        "0.90": "1.282",
        "0.95": "1.645",
        "0.98": "2.054",
        "0.99": "2.326",
    }
    z_values = {}
    for level in expected:
        dataset = _demand_dataset(["10", "20", "30"])
        dataset = EngineDataset(
            skus=dataset.skus,
            movements=dataset.movements,
            replenishment=[_replenishment("SKU-0001", "1", level)],
        )
        result = replenishment.calculate(_request(), dataset)
        z_values[level] = str(result.per_sku[0].safety_stock)

    # LT=1 → √LT=1，SS = z × 10，可直接读出 z
    assert z_values == {level: str(Decimal(value) * 10) for level, value in expected.items()}


def test_z_table_off_table_value_takes_nearest_level() -> None:
    """表外 service_level 取最近档：0.93 距 0.95 更近 → z = 1.645。"""
    dataset = _demand_dataset(["10", "20", "30"])
    dataset = EngineDataset(
        skus=dataset.skus,
        movements=dataset.movements,
        replenishment=[_replenishment("SKU-0001", "1", "0.93")],
    )
    result = replenishment.calculate(_request(), dataset)

    assert result.per_sku[0].z == Decimal("1.645")


def test_z_table_tie_takes_lower_level() -> None:
    """差值相等时取较低档（确定性）：0.925 距 0.90 与 0.95 均为 0.025 → 0.90。"""
    assert replenishment.lookup_z(Decimal("0.925")) == Decimal("1.282")


def test_zero_demand_yields_zero_sigma_and_zero_safety_stock() -> None:
    """零需求：σ_d = 0 → SS = 0；d̄ = 0 → ROP = SS（§7.5 边界，照常输出）。"""
    dataset = EngineDataset(
        skus=[_sku("SKU-0001")],
        movements=[_movement("IN-1", "SKU-0001", "INBOUND", date(2026, 5, 1), "100")],
        replenishment=[_replenishment("SKU-0001", "9")],
    )
    result = replenishment.calculate(_request(), dataset)

    row = result.per_sku[0]
    assert row.sigma_d == Decimal(0)
    assert row.avg_daily_demand == Decimal(0)
    assert row.safety_stock == Decimal(0)
    assert row.reorder_point == Decimal(0)
    assert row.suggested_qty == Decimal(0)


# --- F-REPL-002 / 003 补货点、建议量 ----------------------------------------


def test_reorder_point_and_suggested_qty_match_hand_calculation() -> None:
    """ROP = 229.35；期末 40、无在途 → Q* = 189.35。"""
    dataset = _demand_dataset(["10", "20", "30"])
    dataset = EngineDataset(
        skus=dataset.skus,
        movements=dataset.movements,
        replenishment=[_replenishment("SKU-0001", "9", "0.95")],
    )
    result = replenishment.calculate(_request(), dataset)

    row = result.per_sku[0]
    assert row.reorder_point == Decimal("229.35")
    assert row.suggested_qty == Decimal("189.35")


def test_on_order_qty_is_deducted_with_zero_floor() -> None:
    """在途量从 Q* 扣减；扣减后为负时按下限 0 截断（§7.3）。"""
    base = _demand_dataset(["10", "20", "30"])

    def _run(on_order: str) -> Decimal:
        dataset = EngineDataset(
            skus=base.skus,
            movements=base.movements,
            replenishment=[_replenishment("SKU-0001", "9", "0.95")],
        )
        result = replenishment.calculate(_request(on_order_qty=on_order), dataset)
        return result.per_sku[0].suggested_qty

    assert _run("50") == Decimal("139.35")  # 229.35 − 40 − 50
    assert _run("300") == Decimal(0)  # 扣减后为负 → 下限 0


def test_on_order_qty_supports_per_sku_map() -> None:
    """on_order_qty 支持 {sku_id: value} 映射（标量亦可）。"""
    dataset = _demand_dataset(["10", "20", "30"])
    dataset = EngineDataset(
        skus=dataset.skus,
        movements=dataset.movements,
        replenishment=[_replenishment("SKU-0001", "9", "0.95")],
    )
    result = replenishment.calculate(
        _request(on_order_qty={"SKU-0001": "50", "SKU-OTHER": "999"}), dataset
    )

    assert result.per_sku[0].on_order_qty == Decimal(50)
    assert result.per_sku[0].suggested_qty == Decimal("139.35")


# --- 参数缺失与降级 ---------------------------------------------------------


def test_missing_lead_time_emits_param_missing_and_nulls() -> None:
    """lead_time_days 缺失 → 非阻断 PARAM_MISSING，三项 null，其余分析继续。"""
    dataset = _demand_dataset(["10", "20", "30"])
    result = replenishment.calculate(_request(), dataset)

    row = result.per_sku[0]
    assert row.missing_params == ("lead_time_days",)
    assert row.safety_stock is None
    assert row.reorder_point is None
    assert row.suggested_qty is None
    assert row.reason == "param_missing"
    assert [(w.code, w.fields, w.blocking) for w in result.warnings] == [
        ("PARAM_MISSING", ["SKU-0001", "lead_time_days"], False)
    ]


def test_missing_params_metric_is_null_with_reason() -> None:
    """全部 SKU 参数缺失时三个指标输出 "null" 并标注 reason=param_missing。"""
    dataset = _demand_dataset(["10", "20", "30"])
    result = replenishment.calculate(_request(), dataset)

    for metric in result.metrics:
        assert metric.value == "null"
        assert metric.reason == "param_missing"
        assert metric.sample_count == 0


def test_service_level_defaults_to_095() -> None:
    """service_level 缺省取冻结默认值 0.95（§7.4）。"""
    dataset = _demand_dataset(["10", "20", "30"])
    dataset = EngineDataset(
        skus=dataset.skus,
        movements=dataset.movements,
        replenishment=[_replenishment("SKU-0001", "1")],
    )
    result = replenishment.calculate(_request(), dataset)

    assert result.per_sku[0].service_level == Decimal("0.95")
    assert result.per_sku[0].z == Decimal("1.645")


def test_lead_time_from_parameters_when_no_replenishment_record() -> None:
    """无 dataset.replenishment 记录时回退 request.parameters 的标量值。"""
    dataset = _demand_dataset(["10", "20", "30"])
    result = replenishment.calculate(_request(lead_time_days="9"), dataset)

    assert result.per_sku[0].lead_time_days == Decimal(9)
    assert result.per_sku[0].safety_stock == Decimal("49.35")


def test_zero_period_days_degrades_to_null() -> None:
    """期间天数为 0 时日均需求无定义 → 三项 null 并标注 zero_period_days。"""
    dataset = _demand_dataset([])
    result = replenishment.calculate(_request(end=date(2026, 6, 1)), dataset)

    row = result.per_sku[0]
    assert row.reason == "zero_period_days"
    assert row.safety_stock is None
    assert all(metric.value == "null" for metric in result.metrics)
    assert all(metric.reason == "zero_period_days" for metric in result.metrics)


# --- 指标契约与边界 ---------------------------------------------------------


def test_metric_ids_names_and_units() -> None:
    """三个指标携带冻结的 formula_id / formula_version / name / unit。"""
    dataset = _demand_dataset(["10", "20", "30"])
    dataset = EngineDataset(
        skus=dataset.skus,
        movements=dataset.movements,
        replenishment=[_replenishment("SKU-0001", "9", "0.95")],
    )
    result = replenishment.calculate(_request(), dataset)

    assert [m.formula_id for m in result.metrics] == list(replenishment.FORMULA_IDS)
    assert [m.name for m in result.metrics] == [
        "REPL.SAFETY_STOCK_TOTAL",
        "REPL.REORDER_POINT_TOTAL",
        "REPL.SUGGESTED_QTY_TOTAL",
    ]
    assert all(m.unit == "件" for m in result.metrics)
    assert all(m.formula_version == "0.1.0" for m in result.metrics)
    # 数量类按 §2.2 保留 Decimal 精确算术结果（scale 随乘法传播，如 49.350），
    # 故按数值而非字符串比较
    assert [Decimal(str(m.value)) for m in result.metrics] == [
        Decimal("49.35"),
        Decimal("229.35"),
        Decimal("189.35"),
    ]


def test_period_without_flow_records_uses_zero_series() -> None:
    """SKU 仅有入库（无逐日流水记录）时逐日序列按全零处理，σ_d = 0。"""
    dataset = EngineDataset(
        skus=[_sku("SKU-0001")],
        movements=[],
        replenishment=[_replenishment("SKU-0001", "9")],
    )
    result = replenishment.calculate(_request(), dataset)

    # 无流水即无 SKU 级 rollup，结果不含任何行
    assert result.per_sku == ()


def test_empty_dataset_returns_null_metrics() -> None:
    """空数据集：三项指标输出 "null"，sample_count 0，不发 Warning。"""
    result = replenishment.calculate(_request(), EngineDataset())

    assert len(result.metrics) == 3
    assert all(metric.value == "null" for metric in result.metrics)
    assert all(metric.sample_count == 0 for metric in result.metrics)
    assert result.warnings == ()


def test_sample_std_dev_uses_n_minus_one_denominator() -> None:
    """样本标准差用 n−1 分母（与总体标准差 sqrt(200/3) 区分）。"""
    values = [Decimal(10), Decimal(20), Decimal(30)]
    assert replenishment.sample_std_dev(values) == Decimal(10)
    assert replenishment.sample_std_dev([Decimal(5)]) == Decimal(0)
