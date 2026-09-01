"""分析页 M2 展示测试（Issue #9 指标明细 + Issue #11 告警展示）。

engine 0.3.0 输出五类公式共 18 个指标，原实现把全部指标挤在一个单元格
（``"名称=值单位"`` 分号连接），既无法阅读也丢掉了 ``formula_id`` 与 ``reason``。
M2 改为**指标明细表 + 告警表**两个独立表格，本文件锁定该展示契约：

- 每个指标一行，含 指标 / 值 / 单位 / 公式 ID / 说明；
- 告警按引擎返回顺序逐行展示，含 级别 / 代码 / 说明 / 字段；
- 中文文案**只用于展示**，未知指标名与告警码一律回退英文原名
  （引擎新增指标或告警码时 UI 不阻断、不显示空值）；
- 阻断告警在级别列显式标注「（阻断）」，与非阻断视觉区分。

展示逻辑以合成 ``AnalysisResult`` 驱动（确定性、不依赖数据规模）；
另有一条端到端用例验证真实引擎输出能落入表格。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from contracts import AnalysisResult, InputSummary, ResultMetric, Warning
from contracts.enums import WarningSeverity
from sqlalchemy.orm import Session, sessionmaker
from workbench.application.analysis_usecase import RunAnalysisUseCase
from workbench.infrastructure.db.dataset_adapter import SqliteDatasetAdapter
from workbench.infrastructure.db.result_store import SqlResultStore
from workbench.infrastructure.engine_adapter.providers import LocalEngineProvider
from workbench.presentation.analysis_page import (
    METRIC_LABELS,
    NULL_DISPLAY,
    WARNING_LABELS,
    AnalysisPage,
)

#: 引擎 0.3.0 五类公式共 18 个公式 ID
ALL_FORMULA_IDS = (
    "F-KPI-001",
    "F-KPI-002",
    "F-KPI-003",
    "F-KPI-004",
    "F-KPI-005",
    "F-KPI-006",
    "F-KPI-007",
    "F-KPI-008",
    "F-COGS-001",
    "F-ABC-001",
    "F-AGE-001",
    "F-STALE-001",
    "F-REPL-001",
    "F-REPL-002",
    "F-REPL-003",
    "F-FCST-001",
    "F-FCST-002",
    "F-BM-001",
)


class _StubUseCase:
    """仅满足展示所需的用例替身（展示测试不触发计算）。"""

    def __init__(self, benchmark_version: str = "") -> None:
        self.benchmark_version = benchmark_version


class _StubStore:
    """展示测试不访问持久化。"""

    def list_runs(self) -> list[dict[str, Any]]:
        """空历史。"""
        return []


def _metric(
    name: str,
    value: object = "1.00",
    unit: str = "件",
    formula_id: str = "F-KPI-001",
    reason: str | None = None,
) -> ResultMetric:
    """构造一个指标（补齐必填字段）。"""
    return ResultMetric(
        name=name,
        value=value,
        unit=unit,
        formula_id=formula_id,
        formula_version="0.1.0",
        sample_count=1,
        reason=reason,
    )


def _warning(
    code: str,
    severity: WarningSeverity = WarningSeverity.WARN,
    message: str = "msg",
    fields: list[str] | None = None,
    blocking: bool = False,
) -> Warning:
    """构造一条告警。"""
    return Warning(
        code=code,
        severity=severity,
        message=message,
        fields=fields or [],
        blocking=blocking,
    )


def _result(metrics: list[ResultMetric], warnings: list[Warning]) -> AnalysisResult:
    """构造一个最小可用的分析结果。"""
    return AnalysisResult(
        run_id="run-display-test",
        engine_version="0.3.0",
        formula_version="0.1.0",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 7, 1),
        summary="测试摘要",
        input_summary=InputSummary(
            sku_count=2,
            movement_count=4,
            snapshot_count=1,
            period_start=date(2026, 6, 1),
            period_end=date(2026, 7, 1),
            dataset_digest="a" * 64,
        ),
        metrics=metrics,
        warnings=warnings,
    )


def _column(table: Any, column: int) -> list[str]:
    """取表格某一列的所有文本。"""
    return [table.item(row, column).text() for row in range(table.rowCount())]


def _page(qtbot: Any, benchmark_version: str = "") -> AnalysisPage:
    """构造一个离屏分析页。"""
    page = AnalysisPage(_StubUseCase(benchmark_version), _StubStore())
    qtbot.addWidget(page)
    return page


# ---------------------------------------------------------------- Issue #9


def test_eighteen_metrics_render_one_per_row(qtbot) -> None:
    """18 个指标 → 18 行，不再挤在一个单元格（Issue #9 核心验收）。"""
    page = _page(qtbot)
    metrics = [_metric(f"M{i}", formula_id=fid) for i, fid in enumerate(ALL_FORMULA_IDS)]

    page._show_result(_result(metrics, []))

    assert page.metrics_table.rowCount() == 18
    assert _column(page.metrics_table, 3) == list(ALL_FORMULA_IDS)


def test_metric_row_carries_value_unit_and_reason(qtbot) -> None:
    """每行含 指标 / 值 / 单位 / 公式 ID / 说明 五列。"""
    page = _page(qtbot)
    metrics = [_metric("KPI.TURNOVER", value="4.00", unit="次/期间", formula_id="F-KPI-006",
                       reason="turnover_nonpositive")]

    page._show_result(_result(metrics, []))

    assert _column(page.metrics_table, 0) == [METRIC_LABELS["KPI.TURNOVER"]]
    assert _column(page.metrics_table, 1) == ["4.00"]
    assert _column(page.metrics_table, 2) == ["次/期间"]
    assert _column(page.metrics_table, 3) == ["F-KPI-006"]
    assert _column(page.metrics_table, 4) == ["周转率非正"]


def test_unknown_metric_name_falls_back_to_raw_name(qtbot) -> None:
    """未知指标名回退英文原名，不显示空值（引擎新增指标不阻断 UI）。"""
    page = _page(qtbot)

    page._show_result(_result([_metric("KPI.BRAND_NEW_METRIC")], []))

    assert _column(page.metrics_table, 0) == ["KPI.BRAND_NEW_METRIC"]


def test_null_metric_value_rendered_as_placeholder(qtbot) -> None:
    """指标值为 null（契约中以字符串 "null" 表示）时展示占位符。"""
    page = _page(qtbot)

    page._show_result(_result([_metric("KPI.COVERAGE_DAYS", value="null")], []))

    assert _column(page.metrics_table, 1) == [NULL_DISPLAY]


def test_benchmark_version_shown_in_meta(qtbot) -> None:
    """运行信息区展示基准版本；未配置时明示「未配置」。"""
    page = _page(qtbot, benchmark_version="v0.1.0")
    page._show_result(_result([], []))
    assert page.meta_benchmark.text() == "v0.1.0"

    page_without = _page(qtbot)
    page_without._show_result(_result([], []))
    assert page_without.meta_benchmark.text() == "未配置"


# --------------------------------------------------------------- Issue #11


def test_warnings_render_in_engine_order(qtbot) -> None:
    """告警按引擎返回顺序逐行展示（校验层 → KPI → … → 基准）。"""
    page = _page(qtbot)
    ordered = ["PERIOD_MISMATCH", "UNIT_COST_MISSING", "INSUFFICIENT_SAMPLES",
               "PARAM_MISSING", "BENCHMARK_UNAVAILABLE"]

    page._show_result(_result([], [_warning(code) for code in ordered]))

    assert page.warnings_table.rowCount() == 5
    assert _column(page.warnings_table, 1) == ordered


def test_known_warning_codes_translated_to_chinese(qtbot) -> None:
    """已知告警码展示中文说明。"""
    page = _page(qtbot)

    page._show_result(_result([], [_warning("BENCHMARK_UNAVAILABLE")]))

    assert _column(page.warnings_table, 2) == [WARNING_LABELS["BENCHMARK_UNAVAILABLE"]]
    assert _column(page.warnings_table, 2) != ["BENCHMARK_UNAVAILABLE"]


def test_unknown_warning_code_falls_back_to_raw(qtbot) -> None:
    """未知告警码回退原码，不显示空值（引擎新增告警码不阻断 UI）。"""
    page = _page(qtbot)

    page._show_result(_result([], [_warning("SOME_FUTURE_WARNING")]))

    assert _column(page.warnings_table, 1) == ["SOME_FUTURE_WARNING"]
    assert _column(page.warnings_table, 2) == ["SOME_FUTURE_WARNING"]


def test_blocking_warning_marked_in_level_column(qtbot) -> None:
    """阻断告警在级别列显式标注，与非阻断视觉区分。"""
    page = _page(qtbot)

    page._show_result(
        _result(
            [],
            [
                _warning("UNIT_COST_MISSING", blocking=False),
                _warning("NEGATIVE_BALANCE", severity=WarningSeverity.ERROR, blocking=True),
            ],
        )
    )

    levels = _column(page.warnings_table, 0)
    assert levels[0] == "警告"
    assert "阻断" in levels[1]


def test_warning_fields_rendered_as_text(qtbot) -> None:
    """告警的 fields 以逗号连接展示，便于定位受影响字段。"""
    page = _page(qtbot)

    page._show_result(_result([], [_warning("UNIT_COST_MISSING", fields=["skus", "sku.unit_cost"])]))

    assert _column(page.warnings_table, 3) == ["skus, sku.unit_cost"]


# ------------------------------------------------------------- 端到端验证


def test_real_engine_zero_three_output_fills_tables(
    qtbot,
    seeded_local_db: sessionmaker[Session],
    store: SqlResultStore,
) -> None:
    """端到端：真实 engine 0.3.0 的输出落入指标表与告警表。"""
    use_case = RunAnalysisUseCase(
        LocalEngineProvider(),
        store,
        dataset_provider=SqliteDatasetAdapter(seeded_local_db),
    )
    window = AnalysisPage(use_case, store)
    qtbot.addWidget(window)

    window.run_button.click()

    assert window.meta_engine.text() == "0.3.0"
    assert window.metrics_table.rowCount() > 0
    # 真实引擎不再产生占位告警（PR #14 已实装五类公式）
    codes = _column(window.warnings_table, 1)
    assert "ANALYSIS_PLACEHOLDER" not in codes
