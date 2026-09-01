"""analyze 集成测试：五类公式真实计算、数据质量报告、确定性与序列化往返。

- M2 后 analyze 输出 18 个指标（KPI/COGS 9 + ABC/库龄/呆滞 3 + 补货 3 +
  预测 2 + 基准 1），``ANALYSIS_PLACEHOLDER`` 占位 Warning 已移除；
- 数据质量报告：Warning 按码汇总（计数 + 明细）随 AnalysisResult 返回；
- 确定性：同一输入两次 analyze 序列化结果逐字节一致；
- progress 回调按阶段推进且以 1.0 收尾。
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
from warehouse_engine import WarehouseEngine

from tests.engine.conftest import ALL_M2_FORMULA_IDS

REQUEST = AnalysisRequest(
    run_id="run-analyze-0001",
    start_date=date(2026, 6, 1),
    end_date=date(2026, 6, 30),
    warehouse_ids=["WH-01"],
)


def _dataset() -> EngineDataset:
    """两个 SKU 的稳定数据集：含历史事件、出库与负库存快照。"""
    return EngineDataset(
        skus=[
            SkuRecord(
                sku_id="SKU-0001",
                name="矿泉水 550ml",
                category="饮料",
                unit="瓶",
                unit_cost=Decimal("1.20"),
            ),
            SkuRecord(
                sku_id="SKU-0002",
                name="能量饮料 250ml",
                category="饮料",
                unit="罐",
                unit_cost=Decimal("3.50"),
            ),
        ],
        movements=[
            MovementRecord(
                event_id="EVT-0001",
                sku_id="SKU-0001",
                move_type="INBOUND",
                quantity=Decimal(20),
                move_date=date(2026, 5, 20),
                occurred_at=datetime(2026, 5, 20, 8, 0, tzinfo=UTC),
                warehouse_id="WH-01",
                unit_cost=Decimal("1.20"),
                source=EventSource.IMPORT,
            ),
            MovementRecord(
                event_id="EVT-0002",
                sku_id="SKU-0001",
                move_type="OUTBOUND",
                quantity=Decimal(5),
                move_date=date(2026, 6, 5),
                occurred_at=datetime(2026, 6, 5, 8, 0, tzinfo=UTC),
                warehouse_id="WH-01",
                source=EventSource.IMPORT,
            ),
            MovementRecord(
                event_id="EVT-0003",
                sku_id="SKU-0002",
                move_type="OUTBOUND",
                quantity=Decimal(4),
                move_date=date(2026, 6, 10),
                occurred_at=datetime(2026, 6, 10, 8, 0, tzinfo=UTC),
                warehouse_id="WH-01",
                source=EventSource.IMPORT,
            ),
        ],
        snapshots=[
            SnapshotRecord(
                sku_id="SKU-0002",
                snapshot_date=date(2026, 6, 30),
                quantity=Decimal(-1),
                warehouse_id="WH-01",
                inventory_value=Decimal("-3.50"),
            )
        ],
    )


def test_analyze_returns_all_eighteen_metrics() -> None:
    """analyze 输出五类公式共 18 个指标，每个 formula_id 恰一个。

    指标按计算器分组拼接（KPI → ABC/库龄/呆滞 → 补货 → 预测 → 基准），组内
    顺序由各计算器自行决定（如 F-COGS-001 紧随 F-KPI-005 输出），故此处断言
    集合而非逐项顺序。
    """
    result = WarehouseEngine().analyze(REQUEST, _dataset())

    formula_ids = [metric.formula_id for metric in result.metrics]
    assert len(formula_ids) == 18
    assert set(formula_ids) == set(ALL_M2_FORMULA_IDS)
    assert len(set(formula_ids)) == 18  # 每个 formula_id 恰一个指标
    by_id = {metric.formula_id: metric for metric in result.metrics}
    # SKU-0001：期初 20、出库 5、期末 15；SKU-0002：出库 4、期末 -4（无成本事件）
    assert by_id["F-KPI-001"].value == "20"
    assert by_id["F-KPI-002"].value == "11"
    assert by_id["F-KPI-003"].value == "9"
    # SKU-0001 COGS = 5 × 1.20 = 6.00；SKU-0002 出库时无已知成本记录 → 0
    assert by_id["F-COGS-001"].value == "6.00"


def test_analyze_no_longer_emits_placeholder_warning() -> None:
    """M2 后四类计算器全部实装，结果中不再出现 ANALYSIS_PLACEHOLDER。"""
    result = WarehouseEngine().analyze(REQUEST, _dataset())

    assert [w for w in result.warnings if w.code == "ANALYSIS_PLACEHOLDER"] == []
    # 四类公式的指标均已真实产出
    produced = {metric.formula_id for metric in result.metrics}
    assert {"F-ABC-001", "F-AGE-001", "F-STALE-001"} <= produced
    assert {"F-REPL-001", "F-REPL-002", "F-REPL-003"} <= produced
    assert {"F-FCST-001", "F-FCST-002"} <= produced
    assert "F-BM-001" in produced


def test_analyze_warning_codes_come_from_validation_and_calculators() -> None:
    """warnings 汇总校验层与计算层全部非阻断警告。"""
    result = WarehouseEngine().analyze(REQUEST, _dataset())

    codes = {w.code for w in result.warnings}
    # 校验层：负库存快照
    assert "NEGATIVE_BALANCE" in codes
    # 计算层：SKU-0002 出库无已知成本记录
    assert "UNIT_COST_MISSING" in codes
    # 补货：数据集未提供 lead_time_days 且未配置参数
    assert "PARAM_MISSING" in codes
    # 基准：未注入基准数据（不做经验值兜底）
    assert "BENCHMARK_UNAVAILABLE" in codes
    assert all(w.blocking is False for w in result.warnings)


def test_analyze_builds_data_quality_report() -> None:
    """数据质量报告：按码汇总（计数 + 明细），与 warnings 逐条对应。"""
    result = WarehouseEngine().analyze(REQUEST, _dataset())

    assert result.data_quality is not None
    entries = {entry.code: entry for entry in result.data_quality}
    assert set(entries) >= {
        "NEGATIVE_BALANCE",
        "UNIT_COST_MISSING",
        "PARAM_MISSING",
        "BENCHMARK_UNAVAILABLE",
    }
    assert "ANALYSIS_PLACEHOLDER" not in entries

    # 计数与明细长度一致，且明细就是对应的 Warning
    for code, entry in entries.items():
        matching = [w for w in result.warnings if w.code == code]
        assert entry.count == len(matching)
        assert entry.details == matching

    # 条目按 code 字典序排列（确定性）
    codes = [entry.code for entry in result.data_quality]
    assert codes == sorted(codes)


def test_analyze_deterministic_byte_identical() -> None:
    """同一输入两次 analyze：序列化结果逐字节一致（无随机性/时间依赖）。"""
    engine = WarehouseEngine()
    first = engine.analyze(REQUEST, _dataset())
    second = engine.analyze(REQUEST, _dataset())

    assert first.model_dump_json() == second.model_dump_json()


def test_analyze_progress_reports_stages_and_completes() -> None:
    """progress 回调按阶段推进：单调不减、以 1.0 收尾。"""
    calls: list[float] = []
    result = WarehouseEngine().analyze(REQUEST, _dataset(), progress=calls.append)

    assert calls[0] == 0.0
    assert calls[-1] == 1.0
    assert all(0.0 <= fraction <= 1.0 for fraction in calls)
    assert calls == sorted(calls)
    assert result.metrics  # 结果不受 progress 影响


def test_analyze_empty_dataset_outputs_null_degraded_metrics() -> None:
    """空数据集：可计算的总量指标为 0，依赖样本的指标 null + 原因标注。"""
    request = AnalysisRequest(
        run_id="run-analyze-empty",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 30),
        warehouse_ids=["WH-01"],
    )
    result = WarehouseEngine().analyze(request, EngineDataset())

    by_id = {metric.formula_id: metric for metric in result.metrics}
    assert by_id["F-KPI-001"].value == "0"
    assert by_id["F-KPI-004"].value == "null"
    assert by_id["F-KPI-004"].reason == "empty_dataset"
    assert by_id["F-KPI-004"].sample_count == 0
    assert by_id["F-KPI-008"].value == "null"
    # 空数据集下 M2 的新计算器同样降级：补货 null、预测 null、基准 null
    assert by_id["F-REPL-001"].value == "null"
    assert by_id["F-FCST-001"].value == "null"
    assert by_id["F-BM-001"].value == "null"
    # 数据质量报告已生成：空数据集下无补货参数告警，仅有基准不可用的非阻断提示
    assert result.data_quality is not None
    assert [entry.code for entry in result.data_quality] == ["BENCHMARK_UNAVAILABLE"]


def test_analyze_result_serializes_through_contract_roundtrip() -> None:
    """结果可经契约 JSON 往返验证（data_quality 与 reason 字段不破坏序列化）。"""
    result = WarehouseEngine().analyze(REQUEST, _dataset())

    payload = result.model_dump(mode="json")
    revived = type(result).model_validate(payload)
    assert revived == result
    assert revived.data_quality is not None
    assert revived.metrics[0].formula_version == "0.1.0"
