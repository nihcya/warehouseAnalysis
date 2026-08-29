"""Hypothesis 属性测试：库存守恒、事件数量恒正与确定性。

属性 1 库存守恒：仅有 INBOUND/OUTBOUND 的 movements，
净库存 = sum(INBOUND) − sum(OUTBOUND)，且该守恒在原始字典 → EngineDataset
模型解析（含 Decimal 字符串序列化）往返后保持不变，同时通过引擎校验。
属性 2 事件数量恒正：解析后的所有事件 quantity > 0。
属性 3 确定性：同一 dataset 两次 validate_dataset 逐字段一致，
result.py 的 dataset_digest 稳定（含序列化往返）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from contracts import AnalysisRequest, EngineDataset, MoveType, SkuRecord
from hypothesis import given, settings
from hypothesis import strategies as st
from warehouse_engine import WarehouseEngine
from warehouse_engine.result import build_input_summary

REQUEST = AnalysisRequest(
    run_id="run-property-0001",
    start_date=date(2026, 6, 1),
    end_date=date(2026, 6, 30),
    warehouse_ids=["WH-01"],
)

SKU = SkuRecord(
    sku_id="SKU-0001",
    name="矿泉水 550ml",
    category="饮料",
    unit="瓶",
    unit_cost=Decimal("1.20"),
)

# 期间左闭右开 [2026-06-01, 2026-06-30)，日期均落在期内以隔离 PERIOD_MISMATCH
MOVE_DATES = st.dates(min_value=date(2026, 6, 1), max_value=date(2026, 6, 29))
QUANTITIES = st.integers(min_value=1, max_value=10000)
DIRECTIONS = st.sampled_from([MoveType.INBOUND, MoveType.OUTBOUND])


@st.composite
def inbound_outbound_movements(draw: st.DrawFn) -> list[dict[str, Any]]:
    """随机生成仅含 INBOUND/OUTBOUND 的 movement 原始字典列表。"""
    count = draw(st.integers(min_value=0, max_value=30))
    movements: list[dict[str, Any]] = []
    for index in range(count):
        move_type = draw(DIRECTIONS)
        quantity = draw(QUANTITIES)
        move_date = draw(MOVE_DATES)
        movements.append(
            {
                "event_id": f"EVT-{index:04d}",
                "sku_id": "SKU-0001",
                "move_type": move_type.value,
                "quantity": str(quantity),
                "move_date": move_date.isoformat(),
                "occurred_at": f"{move_date.isoformat()}T08:00:00Z",
                "warehouse_id": "WH-01",
                "source": "IMPORT",
            }
        )
    return movements


def _dataset_of(movements: list[dict[str, Any]]) -> EngineDataset:
    payload = {
        "schema_version": "1.0",
        "skus": [SKU.model_dump(mode="json")],
        "movements": movements,
    }
    return EngineDataset.model_validate(payload)


def _net_inventory(movements: list[dict[str, Any]]) -> Decimal:
    net = Decimal(0)
    for movement in movements:
        sign = 1 if movement["move_type"] == MoveType.INBOUND.value else -1
        net += sign * Decimal(str(movement["quantity"]))
    return net


@settings(deadline=None)
@given(movements=inbound_outbound_movements())
def test_inventory_conservation(movements: list[dict[str, Any]]) -> None:
    """净库存 = sum(INBOUND) − sum(OUTBOUND)，解析往返后守恒且校验通过。"""
    dataset = _dataset_of(movements)
    net_from_models = Decimal(0)
    for movement in dataset.movements:
        sign = 1 if movement.move_type is MoveType.INBOUND else -1
        net_from_models += sign * movement.quantity

    assert net_from_models == _net_inventory(movements)

    report = WarehouseEngine().validate_dataset(REQUEST, dataset)
    assert report.valid is True
    assert report.issues == []
    assert report.warnings == []


@settings(deadline=None)
@given(movements=inbound_outbound_movements())
def test_movement_quantity_always_positive(movements: list[dict[str, Any]]) -> None:
    """事件数量恒正：解析后的每条 movement.quantity > 0。"""
    dataset = _dataset_of(movements)
    assert len(dataset.movements) == len(movements)
    assert all(movement.quantity > 0 for movement in dataset.movements)


@settings(deadline=None)
@given(movements=inbound_outbound_movements())
def test_validate_dataset_and_digest_deterministic(movements: list[dict[str, Any]]) -> None:
    """同一 dataset 两次 validate_dataset 逐字段一致；dataset_digest 稳定。"""
    dataset = _dataset_of(movements)

    engine = WarehouseEngine()
    first = engine.validate_dataset(REQUEST, dataset)
    second = engine.validate_dataset(REQUEST, dataset)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")

    digest_first = build_input_summary(dataset, REQUEST).dataset_digest
    digest_second = build_input_summary(dataset, REQUEST).dataset_digest
    assert digest_first == digest_second

    # 序列化往返（dict → 模型 → dict → 模型）后 digest 仍稳定
    roundtrip = EngineDataset.model_validate(dataset.model_dump(mode="json"))
    assert build_input_summary(roundtrip, REQUEST).dataset_digest == digest_first
