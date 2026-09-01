"""pytest-qt（offscreen）UI 测试：FakeEngine 全链路含展示与按 run_id 重查。

覆盖 §7.2 主窗口骨架与 §7.5 分析结果页：
validate → analyze → 进度条/表格展示 → 持久化 → 按 run_id 重新查看一致；
校验失败时展示错误列表、不调用 analyze、不落库。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QLabel
from workbench.application.analysis_usecase import RunAnalysisUseCase
from workbench.infrastructure.db.result_store import SqlResultStore
from workbench.infrastructure.engine_adapter.providers import FakeEngineProvider
from workbench.presentation.main_window import NAV_ITEMS, MainWindow


def test_main_window_navigation_and_status_bar(
    qtbot,
    fake_provider: FakeEngineProvider,
    store: SqlResultStore,
    golden_input: Path,
) -> None:
    """主窗口：导航条目齐全，状态栏含 网络/授权/待同步/版本 占位。"""
    window = MainWindow(RunAnalysisUseCase(fake_provider, store, golden_input), store)
    qtbot.addWidget(window)

    nav_labels = [window._nav.item(row).text() for row in range(window._nav.count())]
    assert nav_labels == list(NAV_ITEMS)

    status_texts = " ".join(label.text() for label in window.statusBar().findChildren(QLabel))
    for keyword in ("网络", "授权", "待同步", "版本"):
        assert keyword in status_texts
    assert "网络：离线" in status_texts  # M0 离线占位


def test_analysis_page_full_chain_display_and_persistence(
    qtbot,
    fake_provider: FakeEngineProvider,
    store: SqlResultStore,
    golden_input: Path,
) -> None:
    """全链路：点击运行 → 进度满格 → 表格展示 → 落库 → 按 run_id 重查展示一致。"""
    window = MainWindow(RunAnalysisUseCase(fake_provider, store, golden_input), store)
    qtbot.addWidget(window)
    page = window.analysis_page

    page.run_button.click()

    # 进度条由 progress 回调驱动（FakeEngine 一次完成信号 → 100%）
    assert page.progress_bar.value() == 100

    # 运行信息区（M2：由表格首行改为独立元信息区）
    run_id = page.meta_run_id.text()
    assert run_id.startswith("run-")
    assert page.meta_engine.text() == "0.1.0-fake"
    assert page.meta_formula.text() == "0.1.0"

    # 指标明细表（Issue #9）：逐行展示；未知指标名回退英文原名
    assert page.metrics_table.rowCount() > 0
    metric_names = [
        page.metrics_table.item(row, 0).text()
        for row in range(page.metrics_table.rowCount())
    ]
    assert "KPI.OUTBOUND_QTY" in metric_names

    # 告警表（Issue #11）：独立成表，不再挤在一个单元格
    assert page.warnings_table.rowCount() > 0
    warning_codes = [
        page.warnings_table.item(row, 1).text()
        for row in range(page.warnings_table.rowCount())
    ]
    assert "ANALYSIS_PLACEHOLDER" in warning_codes

    # 持久化：按 run_id 读取与展示一致
    stored = store.get(run_id)
    assert stored is not None
    assert stored.run_id == run_id
    assert stored.engine_version == "0.1.0-fake"
    assert [run["run_id"] for run in store.list_runs()] == [run_id]

    # 清空结果区后按 run_id 重新查看：内容恢复一致
    page.metrics_table.setRowCount(0)
    assert page.run_id_input.text() == run_id
    page.view_button.click()
    assert page.metrics_table.rowCount() > 0
    assert page.meta_run_id.text() == run_id
    assert page.meta_engine.text() == "0.1.0-fake"


def test_analysis_page_validation_failure_shows_issues(
    qtbot,
    fake_provider: FakeEngineProvider,
    store: SqlResultStore,
    blocking_input: Path,
) -> None:
    """校验失败：展示错误列表、进度不动、表格为空、无落库。"""
    window = MainWindow(RunAnalysisUseCase(fake_provider, store, blocking_input), store)
    qtbot.addWidget(window)
    page = window.analysis_page

    page.run_button.click()

    assert page.metrics_table.rowCount() == 0
    assert page.warnings_table.rowCount() == 0
    assert page.progress_bar.value() == 0  # 未进入计算阶段
    assert page.issue_list.count() == 1
    assert "DUPLICATE_EVENT" in page.issue_list.item(0).text()
    assert store.list_runs() == []  # 校验失败不落库


def test_analysis_page_view_missing_run_id(
    qtbot,
    fake_provider: FakeEngineProvider,
    store: SqlResultStore,
    golden_input: Path,
) -> None:
    """按不存在的 run_id 查看：给出提示且不崩溃。"""
    window = MainWindow(RunAnalysisUseCase(fake_provider, store, golden_input), store)
    qtbot.addWidget(window)
    page = window.analysis_page

    page.run_id_input.setText("run-not-exist")
    page.view_button.click()

    assert page.metrics_table.rowCount() == 0
    assert page.warnings_table.rowCount() == 0
    assert page.issue_list.count() == 1
    assert "run-not-exist" in page.issue_list.item(0).text()
