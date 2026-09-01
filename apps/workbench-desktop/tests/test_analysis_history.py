"""分析页“历史运行”页测试（M1 Task 7.3，pytest-qt offscreen）。

覆盖：多条 run 记录列表正确（新 → 旧）、双击 / “查看结果”按 run_id
重查结果与持久化一致、本地适配器数据源在 UI 侧的闭环、空库提示。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker
from workbench.application.analysis_usecase import RunAnalysisUseCase
from workbench.infrastructure.db.dataset_adapter import SqliteDatasetAdapter
from workbench.infrastructure.db.result_store import SqlResultStore
from workbench.infrastructure.engine_adapter.providers import FakeEngineProvider
from workbench.presentation.analysis_page import (
    HISTORY_TAB_INDEX,
    NO_DATA_MESSAGE,
    RUN_TAB_INDEX,
)
from workbench.presentation.main_window import MainWindow


def test_history_tab_lists_runs_and_reloads_result(
    qtbot,
    fake_provider: FakeEngineProvider,
    store: SqlResultStore,
    golden_input: Path,
) -> None:
    """历史列表：两次运行两条记录（新 → 旧）；双击 / 按钮按 run_id 加载与持久化一致。"""
    window = MainWindow(RunAnalysisUseCase(fake_provider, store, golden_input), store)
    qtbot.addWidget(window)
    page = window.analysis_page

    page.run_button.click()
    first_run_id = page.meta_run_id.text()
    page.run_button.click()
    second_run_id = page.meta_run_id.text()

    # 切到“历史运行”页：列表新 → 旧，列含期间 / 状态 / 引擎与公式版本 / 时间
    page.tabs.setCurrentIndex(HISTORY_TAB_INDEX)
    assert page.history_table.rowCount() == 2
    assert page.history_table.item(0, 0).text() == second_run_id
    assert page.history_table.item(1, 0).text() == first_run_id
    assert page.history_table.item(0, 1).text() == "2026-06-01 ~ 2026-06-30"
    assert page.history_table.item(0, 2).text() == "成功"
    assert page.history_table.item(0, 3).text() == "0.1.0-fake"
    assert page.history_table.item(0, 4).text() == "0.1.0"
    assert page.history_table.item(0, 5).text() != ""

    # 双击最新一条：结果加载到结果表格并切回“运行分析”页，内容与持久化一致
    page.history_table.itemDoubleClicked.emit(page.history_table.item(0, 0))
    assert page.tabs.currentIndex() == RUN_TAB_INDEX
    assert page.metrics_table.rowCount() > 0
    assert page.meta_run_id.text() == second_run_id
    stored = store.get(second_run_id)
    assert stored is not None
    assert stored.run_id == page.meta_run_id.text()
    assert stored.engine_version == page.meta_engine.text()
    assert stored.formula_version == page.meta_formula.text()

    # 选中另一条后点“查看结果”：加载对应 run 的结果
    page.tabs.setCurrentIndex(HISTORY_TAB_INDEX)
    page.history_table.selectRow(1)
    page.load_run_button.click()
    assert page.tabs.currentIndex() == RUN_TAB_INDEX
    assert page.meta_run_id.text() == first_run_id


def test_local_dataset_full_chain_in_ui(
    qtbot,
    seeded_local_db: sessionmaker[Session],
    store: SqlResultStore,
    fake_provider: FakeEngineProvider,
) -> None:
    """本地数据源 UI 闭环：导入样本后运行分析 → 结果展示 → 历史运行可重查。"""
    window = MainWindow(
        RunAnalysisUseCase(
            fake_provider, store, dataset_provider=SqliteDatasetAdapter(seeded_local_db)
        ),
        store,
    )
    qtbot.addWidget(window)
    page = window.analysis_page

    page.run_button.click()

    assert page.progress_bar.value() == 100
    assert page.metrics_table.rowCount() > 0
    run_id = page.meta_run_id.text()
    assert page.meta_engine.text() == "0.1.0-fake"

    page.tabs.setCurrentIndex(HISTORY_TAB_INDEX)
    assert page.history_table.rowCount() == 1
    assert page.history_table.item(0, 0).text() == run_id

    # 双击历史记录：重查结果与持久化一致
    page.history_table.itemDoubleClicked.emit(page.history_table.item(0, 0))
    assert page.meta_run_id.text() == run_id
    stored = store.get(run_id)
    assert stored is not None
    assert stored.run_id == run_id


def test_empty_local_database_shows_no_data_hint(
    qtbot,
    session_factory: sessionmaker[Session],
    store: SqlResultStore,
    fake_provider: FakeEngineProvider,
) -> None:
    """空库运行分析：展示“无数据”提示，不异常、不落库、历史列表为空。"""
    window = MainWindow(
        RunAnalysisUseCase(
            fake_provider, store, dataset_provider=SqliteDatasetAdapter(session_factory)
        ),
        store,
    )
    qtbot.addWidget(window)
    page = window.analysis_page

    page.run_button.click()

    assert page.metrics_table.rowCount() == 0
    assert page.warnings_table.rowCount() == 0
    assert page.progress_bar.value() == 0  # 未进入计算阶段
    assert page.issue_list.count() == 1
    assert page.issue_list.item(0).text() == NO_DATA_MESSAGE
    assert page.history_table.rowCount() == 0
    assert store.list_runs() == []
