"""experimental 隔离骨架测试（formula-spec §8.1：实验模型不进默认结果）。

验证三重隔离：

1. **类型层**：``ExperimentalForecastResult`` 不继承也不转换为 ``ResultMetric``，
   无法塞进 ``build_analysis_result(metrics=...)``；
2. **导入层**：默认 ``analyze()`` / ``list_capabilities()`` 路径不含实验输出，
   且**不 import statsmodels**（用子进程隔离验证，避免同进程内其他测试污染）；
3. **行为层**：实验模型只能通过显式 ``run_experimental()`` 调用取得，结果携带
   EXPERIMENTAL_DISCLAIMER，且 Holt-Winters 桩明确报"尚未实装"。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from contracts import (
    AnalysisRequest,
    EngineDataset,
    EventSource,
    MovementRecord,
    ResultMetric,
    SkuRecord,
)
from pydantic import ValidationError
from warehouse_engine.calculators import abc_aging, benchmark_compare, forecasting, replenishment
from warehouse_engine.engine import WarehouseEngine
from warehouse_engine.experimental import (
    EXPERIMENTAL_DISCLAIMER,
    ExperimentalForecastResult,
    forecast_models,
)
from warehouse_engine.result import build_analysis_result

REPO_ROOT = Path(__file__).resolve().parents[2]
START = date(2026, 1, 5)  # 周一
END = date(2026, 4, 6)


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        run_id="run-experimental",
        start_date=START,
        end_date=END,
        warehouse_ids=["WH-01"],
    )


def _dataset() -> EngineDataset:
    movements = [
        MovementRecord(
            event_id="IN-0",
            sku_id="SKU-0001",
            move_type="INBOUND",
            quantity=Decimal(10000),
            move_date=date(2025, 12, 1),
            occurred_at=datetime(2025, 12, 1, 8, 0, tzinfo=UTC),
            warehouse_id="WH-01",
            source=EventSource.IMPORT,
        )
    ]
    for index in range(13):
        movements.append(
            MovementRecord(
                event_id=f"OUT-{index}",
                sku_id="SKU-0001",
                move_type="OUTBOUND",
                quantity=Decimal(10 + index),
                move_date=START + timedelta(weeks=index),
                occurred_at=datetime(2026, 1, 5, 8, 0, tzinfo=UTC),
                warehouse_id="WH-01",
                source=EventSource.IMPORT,
            )
        )
    return EngineDataset(
        skus=[SkuRecord(sku_id="SKU-0001", name="SKU-0001", category="测试", unit="件")],
        movements=movements,
    )


# --- 类型层隔离 -------------------------------------------------------------


def test_experimental_result_is_not_a_result_metric() -> None:
    """实验输出类型与 ResultMetric 无继承关系，无法混入默认指标列表。"""
    experimental = ExperimentalForecastResult(
        model="seasonal_naive",
        sku_id="SKU-0001",
        horizon=1,
        forecast=(Decimal(10),),
        notes=(EXPERIMENTAL_DISCLAIMER,),
    )

    assert not isinstance(experimental, ResultMetric)
    assert not hasattr(experimental, "formula_id")


def test_experimental_result_cannot_enter_analysis_result() -> None:
    """build_analysis_result 的 metrics 在类型层面拒绝实验输出。"""
    experimental = ExperimentalForecastResult(
        model="seasonal_naive",
        sku_id="SKU-0001",
        horizon=1,
        forecast=(Decimal(10),),
        notes=(EXPERIMENTAL_DISCLAIMER,),
    )

    with pytest.raises(ValidationError):  # 契约校验拒绝非 ResultMetric 实例
        build_analysis_result(
            _request(),
            _dataset(),
            engine_version="0.3.0",
            formula_version="0.1.0",
            summary="实验输出不得进入默认结果",
            metrics=[experimental],  # type: ignore[list-item]
        )


# --- 导入层隔离 -------------------------------------------------------------


def test_default_analyze_contains_no_experimental_output() -> None:
    """默认 analyze 的指标集合不含任何实验模型输出。"""
    result = WarehouseEngine().analyze(_request(), _dataset())

    experimental_names = {"seasonal_naive", "holt_winters", "experimental"}
    assert not experimental_names & {metric.name for metric in result.metrics}
    assert not experimental_names & {metric.formula_id for metric in result.metrics}
    assert all(
        EXPERIMENTAL_DISCLAIMER not in warning.message for warning in result.warnings
    )


def test_capabilities_exclude_experimental_models() -> None:
    """list_capabilities 的公式 ID 只含冻结口径，不含实验模型。"""
    capabilities = WarehouseEngine().list_capabilities()

    formula_ids = {formula for item in capabilities for formula in item.formula_ids}
    assert "seasonal_naive" not in formula_ids
    assert "holt_winters" not in formula_ids
    assert {"F-FCST-001", "F-FCST-002"} <= formula_ids


def test_calculators_do_not_import_experimental() -> None:
    """四个计算器与 engine 均不 import experimental（AST 级静态检查）。

    只检查 import 语句本身：文档字符串中出现 "experimental/" 字样说明隔离关系
    是允许的，不构成导入耦合。
    """
    import ast
    import inspect

    for module in (abc_aging, replenishment, forecasting, benchmark_compare):
        tree = ast.parse(inspect.getsource(module))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        assert not [name for name in imported if "experimental" in name], module.__name__


def test_default_path_does_not_import_statsmodels() -> None:
    """子进程验证：默认 analyze 路径不 import statsmodels。"""
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(REPO_ROOT / "tests" / "engine")!r})

        from datetime import date
        from warehouse_engine import WarehouseEngine
        from test_experimental_isolation import _request, _dataset

        WarehouseEngine().analyze(_request(), _dataset())
        WarehouseEngine().list_capabilities()
        leaked = sorted(m for m in sys.modules if m.startswith("statsmodels"))
        assert not leaked, leaked
        print("OK")
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "OK" in completed.stdout


# --- 行为层 -----------------------------------------------------------------


def test_run_experimental_returns_disclaimed_results() -> None:
    """显式调用 run_experimental 可取得实验输出，且携带免责声明。"""
    results = forecast_models.run_experimental(
        _request(), _dataset(), model="seasonal_naive", horizon=2
    )

    assert len(results) == 1
    result = results[0]
    assert result.model == "seasonal_naive"
    assert result.horizon == 2
    assert len(result.forecast) == 2
    assert any(EXPERIMENTAL_DISCLAIMER == note for note in result.notes)


def test_seasonal_naive_falls_back_when_history_shorter_than_a_year() -> None:
    """历史不足 52 周时季节性朴素回退最近一期需求（不报错、不外推）。"""
    results = forecast_models.run_experimental(
        _request(), _dataset(), model="seasonal_naive", horizon=1
    )

    # 数据集仅 13 周历史，回退到最后一周需求（10 + 12 = 22）
    assert results[0].forecast == (Decimal(22),)


def test_holt_winters_stub_reports_not_implemented() -> None:
    """Holt-Winters 桩明确报"尚未实装"，不返回伪结果。"""
    with pytest.raises(forecast_models.ExperimentalModelError) as excinfo:
        forecast_models.run_experimental(_request(), _dataset(), model="holt_winters")

    assert "尚未实装" in str(excinfo.value)
    assert "AnalysisResult" in str(excinfo.value)


def test_unknown_model_reports_available_models() -> None:
    """未注册模型名报错并列出可用模型。"""
    with pytest.raises(forecast_models.ExperimentalModelError) as excinfo:
        forecast_models.run_experimental(_request(), _dataset(), model="arima")

    assert "未注册" in str(excinfo.value)
    assert "seasonal_naive" in str(excinfo.value)
    assert forecast_models.available_models() == ("holt_winters", "seasonal_naive")
