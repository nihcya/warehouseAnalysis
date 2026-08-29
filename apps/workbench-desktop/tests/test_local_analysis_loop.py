"""本地业务闭环测试（M1 Task 7.4）：导入 → 适配 → 校验 → FakeEngine 分析 → 落库。

数据源使用 SqliteDatasetAdapter（M1 默认），引擎注入方式与 M0 一致
（FakeEngineProvider）；空库 / 仅主数据时返回“无数据”结果，不抛异常。
"""

from __future__ import annotations

from datetime import date

from local_data.repository import MasterDataRepository
from sqlalchemy.orm import Session, sessionmaker
from workbench.application.analysis_usecase import RunAnalysisUseCase
from workbench.infrastructure.db.dataset_adapter import SqliteDatasetAdapter
from workbench.infrastructure.db.result_store import SqlResultStore
from workbench.infrastructure.engine_adapter.providers import FakeEngineProvider


def test_import_to_analysis_full_chain(
    seeded_local_db: sessionmaker[Session],
    store: SqlResultStore,
    fake_provider: FakeEngineProvider,
) -> None:
    """全链路：CSV 导入样本 → 本地适配器构造数据集 → 校验 → FakeEngine 分析 → 持久化。"""
    use_case = RunAnalysisUseCase(
        fake_provider, store, dataset_provider=SqliteDatasetAdapter(seeded_local_db)
    )
    progresses: list[float] = []
    outcome = use_case.run(progress=progresses.append)

    assert outcome.ok
    assert outcome.result is not None
    assert outcome.issues == []
    assert progresses == [1.0]  # FakeEngine progress 仅一次完成信号
    # 结果来自 FakeEngine 冻结 fixture，run_id 与分析期间为本地请求的推导值
    assert outcome.result.run_id == outcome.run_id
    assert outcome.result.engine_version == "0.1.0-fake"
    assert outcome.result.period_start == date(2026, 6, 2)
    assert outcome.result.period_end == date(2026, 6, 30)

    # 持久化：按 run_id 取回一致；运行列表登记
    assert store.get(outcome.run_id) == outcome.result
    assert [run["run_id"] for run in store.list_runs()] == [outcome.run_id]


def test_repeated_local_runs_generate_new_run_ids(
    seeded_local_db: sessionmaker[Session],
    store: SqlResultStore,
    fake_provider: FakeEngineProvider,
) -> None:
    """重复运行：每次生成新 run_id，历史运行列表按新 → 旧排列。"""
    use_case = RunAnalysisUseCase(
        fake_provider, store, dataset_provider=SqliteDatasetAdapter(seeded_local_db)
    )
    first = use_case.run()
    second = use_case.run()

    assert first.ok and second.ok
    assert first.run_id != second.run_id
    assert [run["run_id"] for run in store.list_runs()] == [second.run_id, first.run_id]


def test_empty_database_returns_no_data(
    session_factory: sessionmaker[Session],
    store: SqlResultStore,
    fake_provider: FakeEngineProvider,
) -> None:
    """空库：返回明确“无数据”结果，不校验、不计算、不落库、不抛异常。"""
    use_case = RunAnalysisUseCase(
        fake_provider, store, dataset_provider=SqliteDatasetAdapter(session_factory)
    )
    outcome = use_case.run()

    assert not outcome.ok
    assert outcome.no_data
    assert outcome.result is None
    assert outcome.issues == []
    assert store.list_runs() == []


def test_master_data_only_returns_no_data(
    session_factory: sessionmaker[Session],
    store: SqlResultStore,
    fake_provider: FakeEngineProvider,
) -> None:
    """仅导入主数据（无事件/快照）：同样返回“无数据”提示。"""
    master = MasterDataRepository(session_factory)
    master.add_warehouse(warehouse_id="WH-01", name="主仓")
    master.add_sku(sku_id="SKU-0001", name="矿泉水 550ml")

    use_case = RunAnalysisUseCase(
        fake_provider, store, dataset_provider=SqliteDatasetAdapter(session_factory)
    )
    outcome = use_case.run()

    assert outcome.no_data
    assert store.list_runs() == []
