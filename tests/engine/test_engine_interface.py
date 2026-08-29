"""引擎接口契约测试：阻断数据抛 DataValidationError；analyze 不修改传入对象。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from contracts import (
    AnalysisRequest,
    EngineDataset,
    EventSource,
    MovementRecord,
    SkuRecord,
)
from warehouse_engine import DataValidationError, FakeEngine, WarehouseEngine

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "fake-analysis.json"
)


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        run_id="run-interface-0001",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 30),
        warehouse_ids=["WH-01"],
    )


def _movement(event_id: str, move_type: str, move_date: date) -> MovementRecord:
    return MovementRecord(
        event_id=event_id,
        sku_id="SKU-0001",
        move_type=move_type,
        quantity=Decimal(10),
        move_date=move_date,
        occurred_at=datetime(2026, 6, 5, 10, 0, tzinfo=UTC),
        warehouse_id="WH-01",
        source=EventSource.IMPORT,
    )


def _blocking_dataset() -> EngineDataset:
    """重复 event_id 的数据集（DUPLICATE_EVENT 阻断）。"""
    return EngineDataset(
        skus=[
            SkuRecord(
                sku_id="SKU-0001",
                name="矿泉水 550ml",
                category="饮料",
                unit="瓶",
                unit_cost=Decimal("1.20"),
            )
        ],
        movements=[
            _movement("EVT-DUP-0001", "OUTBOUND", date(2026, 6, 5)),
            _movement("EVT-DUP-0001", "OUTBOUND", date(2026, 6, 5)),
        ],
    )


def _valid_dataset() -> EngineDataset:
    return EngineDataset(
        skus=[
            SkuRecord(
                sku_id="SKU-0001",
                name="矿泉水 550ml",
                category="饮料",
                unit="瓶",
                unit_cost=Decimal("1.20"),
            )
        ],
        movements=[
            _movement("EVT-0001", "INBOUND", date(2026, 6, 2)),
            _movement("EVT-0002", "OUTBOUND", date(2026, 6, 5)),
        ],
    )


@pytest.mark.parametrize(
    "engine_factory",
    [WarehouseEngine, lambda: FakeEngine.from_fixture(FIXTURE_PATH)],
    ids=["warehouse-engine", "fake-engine"],
)
def test_analyze_raises_data_validation_error_on_blocking_dataset(
    engine_factory: Callable[[], WarehouseEngine | FakeEngine],
) -> None:
    engine = engine_factory()
    with pytest.raises(DataValidationError) as exc_info:
        engine.analyze(_request(), _blocking_dataset())

    error = exc_info.value
    assert error.code.value == "DATA_VALIDATION_FAILED"
    assert error.details
    assert any(detail["field"] == "movements.event_id" for detail in error.details)


@pytest.mark.parametrize(
    "engine_factory",
    [WarehouseEngine, lambda: FakeEngine.from_fixture(FIXTURE_PATH)],
    ids=["warehouse-engine", "fake-engine"],
)
def test_analyze_does_not_mutate_input_dataset(
    engine_factory: Callable[[], WarehouseEngine | FakeEngine],
) -> None:
    engine = engine_factory()
    dataset = _valid_dataset()
    before = dataset.model_dump_json()

    engine.analyze(_request(), dataset)

    assert dataset.model_dump_json() == before
