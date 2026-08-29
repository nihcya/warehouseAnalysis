"""FakeEngine 测试：from_fixture 加载、request 覆盖、版本字段与 progress 回调。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from contracts import (
    AnalysisRequest,
    AnalysisResult,
    EngineDataset,
    EventSource,
    MovementRecord,
    SkuRecord,
)
from warehouse_engine import FakeEngine, WarehouseEngine

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "fake-analysis.json"
)


def _request() -> AnalysisRequest:
    # 期间与 fixture 冻结值不同，用于验证覆盖行为
    return AnalysisRequest(
        run_id="run-fake-9999",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
        warehouse_ids=["WH-01"],
    )


def _dataset() -> EngineDataset:
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
            MovementRecord(
                event_id="EVT-0001",
                sku_id="SKU-0001",
                move_type="INBOUND",
                quantity=Decimal(10),
                move_date=date(2026, 7, 2),
                occurred_at=datetime(2026, 7, 2, 9, 0, tzinfo=UTC),
                warehouse_id="WH-01",
                source=EventSource.IMPORT,
            )
        ],
    )


def test_from_fixture_loads_analysis_result() -> None:
    engine = FakeEngine.from_fixture(FIXTURE_PATH)
    assert engine.engine_version == "0.1.0-fake"
    assert engine.formula_version == "0.1.0"


def test_analyze_overrides_run_id_and_period() -> None:
    engine = FakeEngine.from_fixture(FIXTURE_PATH)
    request = _request()
    result = engine.analyze(request, _dataset())

    assert result.run_id == "run-fake-9999"
    assert result.period_start == date(2026, 7, 1)
    assert result.period_end == date(2026, 7, 31)


def test_analyze_keeps_other_fixture_fields() -> None:
    engine = FakeEngine.from_fixture(FIXTURE_PATH)
    result = engine.analyze(_request(), _dataset())

    fixture = AnalysisResult.model_validate(
        json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    )
    # 其余字段原样（metrics、warnings、summary、input_summary 均来自 fixture）
    assert result.metrics == fixture.metrics
    assert result.warnings == fixture.warnings
    assert result.summary == fixture.summary
    assert result.input_summary == fixture.input_summary


def test_analyze_result_validates_with_versions() -> None:
    engine = FakeEngine.from_fixture(FIXTURE_PATH)
    result = engine.analyze(_request(), _dataset())

    validated = AnalysisResult.model_validate(result.model_dump(mode="json"))
    assert validated.schema_version == "1.0"
    assert validated.engine_version == "0.1.0-fake"
    assert validated.formula_version == "0.1.0"
    assert any(w.code == "ANALYSIS_PLACEHOLDER" for w in validated.warnings)


def test_progress_callback_called_once() -> None:
    engine = FakeEngine.from_fixture(FIXTURE_PATH)
    calls: list[float] = []
    result = engine.analyze(_request(), _dataset(), progress=calls.append)

    assert calls == [1.0]
    assert result.run_id == "run-fake-9999"


def test_list_capabilities_matches_real_engine() -> None:
    fake = FakeEngine.from_fixture(FIXTURE_PATH)
    assert fake.list_capabilities() == WarehouseEngine().list_capabilities()
