"""Task 1 校验规则测试：冲销引用（§2.6）与盘点实盘数量（§2.5）。

- REVERSAL 缺少 reversal_of / 引用不存在 / 引用链成环（含自引用）：阻断
  （DATA_VALIDATION_FAILED，定位到事件行）；
- STOCKTAKE 实盘数量非正：阻断（契约层 quantity>0 拦截后，本规则作为
  重放内核的防御性保证，用 model_construct 绕过模型校验直接测规则）；
- 合法冲销（含链式冲销）通过校验。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from contracts import (
    AnalysisRequest,
    EngineDataset,
    EventSource,
    MovementRecord,
    MoveType,
    SkuRecord,
)
from warehouse_engine.validation.rules import apply_dataset_rules

REQUEST = AnalysisRequest(
    run_id="run-rules-0001",
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


def _movement(
    event_id: str,
    move_type: MoveType | str,
    *,
    reversal_of: str | None = None,
    quantity: str = "10",
) -> MovementRecord:
    return MovementRecord(
        event_id=event_id,
        sku_id="SKU-0001",
        move_type=move_type,
        quantity=Decimal(quantity),
        move_date=date(2026, 6, 5),
        occurred_at=datetime(2026, 6, 5, 8, 0, tzinfo=UTC),
        warehouse_id="WH-01",
        source=EventSource.IMPORT,
        reversal_of=reversal_of,
    )


def _dataset(movements: list[MovementRecord]) -> EngineDataset:
    return EngineDataset(skus=[SKU], movements=movements)


def _issue_fields(
    movements: list[MovementRecord],
) -> list[tuple[str, str, int | None]]:
    issues, _ = apply_dataset_rules(_dataset(movements), REQUEST)
    return [(issue.field, issue.reason, issue.row) for issue in issues]


def test_reversal_without_reference_blocks() -> None:
    """REVERSAL 缺少 reversal_of：阻断并定位到该事件。"""
    movements = [
        _movement("EVT-1", "INBOUND"),
        _movement("EVT-R", "REVERSAL", reversal_of=None),
    ]
    issues, _ = apply_dataset_rules(_dataset(movements), REQUEST)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.code.value == "DATA_VALIDATION_FAILED"
    assert issue.field == "movements.reversal_of"
    assert issue.row == 1
    assert "EVT-R" in issue.reason


def test_reversal_referencing_unknown_event_blocks() -> None:
    """reversal_of 引用不存在的 event_id：阻断并定位到该事件。"""
    movements = [
        _movement("EVT-1", "INBOUND"),
        _movement("EVT-R", "REVERSAL", reversal_of="EVT-NONE"),
    ]
    issues, _ = apply_dataset_rules(_dataset(movements), REQUEST)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.code.value == "DATA_VALIDATION_FAILED"
    assert issue.field == "movements.reversal_of"
    assert "EVT-NONE" in issue.reason


def test_reversal_reference_cycle_blocks() -> None:
    """冲销引用链成环（互相引用）：阻断。"""
    movements = [
        _movement("EVT-R1", "REVERSAL", reversal_of="EVT-R2"),
        _movement("EVT-R2", "REVERSAL", reversal_of="EVT-R1"),
    ]
    fields = _issue_fields(movements)
    assert fields
    assert all(field == "movements.reversal_of" for field, _, _ in fields)
    assert all("环" in reason for _, reason, _ in fields)


def test_reversal_self_reference_blocks() -> None:
    """冲销自引用：阻断。"""
    movements = [_movement("EVT-R", "REVERSAL", reversal_of="EVT-R")]
    fields = _issue_fields(movements)
    assert fields
    assert fields[0][0] == "movements.reversal_of"


def test_valid_reversal_passes_validation() -> None:
    """合法冲销（引用存在的基础事件）：无阻断 issue。"""
    movements = [
        _movement("EVT-1", "INBOUND"),
        _movement("EVT-R", "REVERSAL", reversal_of="EVT-1"),
    ]
    issues, _ = apply_dataset_rules(_dataset(movements), REQUEST)
    assert issues == []


def test_valid_chained_reversal_passes_validation() -> None:
    """链式冲销（撤销的撤销）：引用链合法即通过。"""
    movements = [
        _movement("EVT-1", "INBOUND"),
        _movement("EVT-R1", "REVERSAL", reversal_of="EVT-1"),
        _movement("EVT-R2", "REVERSAL", reversal_of="EVT-R1"),
    ]
    issues, _ = apply_dataset_rules(_dataset(movements), REQUEST)
    assert issues == []


def test_stocktake_non_positive_quantity_blocks() -> None:
    """STOCKTAKE 实盘数量非正：阻断（防御性规则，绕过模型 gt=0 约束直接验证）。"""
    stocktake = MovementRecord.model_construct(
        event_id="EVT-S",
        sku_id="SKU-0001",
        move_type=MoveType.STOCKTAKE,
        quantity=Decimal(0),
        move_date=date(2026, 6, 5),
        occurred_at=datetime(2026, 6, 5, 8, 0, tzinfo=UTC),
        warehouse_id="WH-01",
        unit_cost=None,
        lot_id=None,
        source=EventSource.ADJUSTMENT,
        reversal_of=None,
    )
    dataset = EngineDataset.model_construct(
        schema_version="1.0",
        skus=[SKU],
        movements=[stocktake],
        snapshots=[],
        replenishment=[],
    )
    issues, _ = apply_dataset_rules(dataset, REQUEST)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.code.value == "DATA_VALIDATION_FAILED"
    assert issue.field == "movements.quantity"
    assert "EVT-S" in issue.reason


def test_positive_stocktake_passes_validation() -> None:
    """正实盘数量的盘点事件：无阻断 issue。"""
    movements = [
        _movement("EVT-1", "INBOUND"),
        _movement("EVT-S", "STOCKTAKE", quantity="7"),
    ]
    issues, _ = apply_dataset_rules(_dataset(movements), REQUEST)
    assert issues == []
