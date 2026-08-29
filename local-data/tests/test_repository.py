"""Repository 测试：AnalysisResult 存取往返一致。"""

from __future__ import annotations

from datetime import date

from contracts import AnalysisResult, InputSummary, ResultMetric, Warning
from local_data.models import RUN_STATUS_SUCCEEDED
from local_data.repository import AnalysisRepository
from sqlalchemy.orm import Session, sessionmaker


def _make_result(run_id: str = "run-test-0001") -> AnalysisResult:
    """构造最小可用 AnalysisResult（满足 contracts 必填字段）。"""
    return AnalysisResult(
        schema_version="1.0",
        run_id=run_id,
        engine_version="0.1.0-fake",
        formula_version="0.1.0",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        metrics=[
            ResultMetric(
                name="KPI.OUTBOUND_QTY",
                value="60.5",  # 金额/数量为字符串（Decimal 序列化，禁止 float 真值）
                unit="件",
                formula_id="F-KPI-003",
                formula_version="0.1.0",
                sample_count=3,
            ),
            ResultMetric(
                name="KPI.TURNOVER_RATE",
                value=2.4,
                unit="次/期间",
                formula_id="F-KPI-006",
                formula_version="0.1.0",
                sample_count=3,
            ),
        ],
        warnings=[
            Warning(
                code="ANALYSIS_PLACEHOLDER",
                severity="INFO",
                message="M0 占位结果",
                fields=[],
                blocking=False,
            )
        ],
        summary="M0 占位结果",
        input_summary=InputSummary(
            sku_count=3,
            movement_count=12,
            snapshot_count=3,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            dataset_digest=(
                "03bb187bd4ff9ee5d0dc88ae669ece94746f644cbbe342610930ede554627fe8"
            ),
        ),
    )


def test_save_and_get_round_trip(session_factory: sessionmaker[Session]) -> None:
    """save_result → get_result 往返一致（run_id/engine_version/formula_version/metrics）。"""
    repo = AnalysisRepository(session_factory)
    result = _make_result()
    run_id = repo.save_result(result, task_id="task-001")
    assert run_id == result.run_id

    loaded = repo.get_result(run_id)
    assert loaded is not None
    assert loaded == result  # 模型整体往返一致
    assert loaded.run_id == result.run_id
    assert loaded.engine_version == result.engine_version
    assert loaded.formula_version == result.formula_version
    assert len(loaded.metrics) == len(result.metrics)
    assert loaded.input_summary == result.input_summary


def test_get_result_missing_run_returns_none(session_factory: sessionmaker[Session]) -> None:
    """不存在的 run_id 返回 None。"""
    repo = AnalysisRepository(session_factory)
    assert repo.get_result("run-not-exist") is None


def test_list_runs_summary(session_factory: sessionmaker[Session]) -> None:
    """list_runs 返回最近运行摘要（run_id/task_id/status/版本/日期）。"""
    repo = AnalysisRepository(session_factory)
    repo.save_result(_make_result("run-list-0001"), task_id="task-A")
    repo.save_result(_make_result("run-list-0002"))

    runs = repo.list_runs()
    assert len(runs) == 2
    assert {run["run_id"] for run in runs} == {"run-list-0001", "run-list-0002"}

    by_run = {run["run_id"]: run for run in runs}
    first = by_run["run-list-0001"]
    assert first["task_id"] == "task-A"
    assert first["status"] == RUN_STATUS_SUCCEEDED
    assert first["engine_version"] == "0.1.0-fake"
    assert first["formula_version"] == "0.1.0"
    assert first["start_date"] == "2026-08-01"
    assert first["end_date"] == "2026-08-31"
    assert first["created_at"] is not None
    assert by_run["run-list-0002"]["task_id"] is None


def test_list_runs_limit(session_factory: sessionmaker[Session]) -> None:
    """limit 参数生效（按创建时间倒序取前 N 条）。"""
    repo = AnalysisRepository(session_factory)
    for index in range(5):
        repo.save_result(_make_result(f"run-limit-{index:04d}"))
    runs = repo.list_runs(limit=3)
    assert len(runs) == 3
    # 倒序：最新的三个 run
    assert {run["run_id"] for run in runs} == {
        "run-limit-0004",
        "run-limit-0003",
        "run-limit-0002",
    }
