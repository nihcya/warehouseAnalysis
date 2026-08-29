"""分析用例与组合根测试（无 UI）。

- golden 输入全链路：validate → analyze(progress) → 落库 → 按 run_id 取回一致；
- 校验失败：返回错误列表、不调用 analyze、不落库；
- 组合根：``WORKBENCH_ENGINE`` 分支只存在于 ``app.main``。
"""

from __future__ import annotations

from pathlib import Path

from contracts import AnalysisRequest, AnalysisResult, EngineDataset, ValidationReport
from workbench.application.analysis_usecase import (
    AnalysisOutcome,
    RunAnalysisUseCase,
    load_analysis_inputs,
)
from workbench.domain.engine_provider import ProgressCallback
from workbench.infrastructure.db.result_store import SqlResultStore
from workbench.infrastructure.engine_adapter.providers import (
    FakeEngineProvider,
    LocalEngineProvider,
)
from workbench.main import create_engine_provider


class _CountingProvider:
    """包装提供方：统计 analyze 调用次数，验证校验失败时不进入计算。"""

    def __init__(self, inner: FakeEngineProvider) -> None:
        self._inner = inner
        self.analyze_calls = 0

    @property
    def engine_version(self) -> str:
        return self._inner.engine_version

    @property
    def formula_version(self) -> str:
        return self._inner.formula_version

    def validate_dataset(
        self,
        request: AnalysisRequest,
        dataset: EngineDataset,
    ) -> ValidationReport:
        return self._inner.validate_dataset(request, dataset)

    def analyze(
        self,
        request: AnalysisRequest,
        dataset: EngineDataset,
        progress: ProgressCallback | None = None,
    ) -> AnalysisResult:
        self.analyze_calls += 1
        return self._inner.analyze(request, dataset, progress)


def test_load_analysis_inputs_reads_golden_structure(golden_input: Path) -> None:
    """golden input.json 为 {"request", "dataset"} 结构，可构造契约模型。"""
    request, dataset = load_analysis_inputs(golden_input)
    assert request.run_id == "run-golden-0001"
    assert request.start_date.isoformat() == "2026-06-01"
    assert len(dataset.skus) == 3
    assert dataset.movements and dataset.snapshots


def test_run_golden_input_full_chain(
    fake_provider: FakeEngineProvider,
    store: SqlResultStore,
    golden_input: Path,
) -> None:
    """FakeEngine 全链路：校验通过 → 计算（progress 一次完成信号）→ 落库可取回一致。"""
    progresses: list[float] = []
    outcome = RunAnalysisUseCase(fake_provider, store, golden_input).run(
        progress=progresses.append
    )

    assert outcome.ok
    assert outcome.issues == []
    assert outcome.result is not None
    assert outcome.result.run_id == outcome.run_id
    assert outcome.result.engine_version == "0.1.0-fake"
    assert progresses == [1.0]  # FakeEngine progress 仅一次完成信号

    # 持久化后按 run_id 读取一致（AnalysisResult 模型等值比较）
    stored = store.get(outcome.run_id)
    assert stored == outcome.result
    assert [run["run_id"] for run in store.list_runs()] == [outcome.run_id]


def test_run_generates_fresh_run_id_each_time(
    fake_provider: FakeEngineProvider,
    store: SqlResultStore,
    golden_input: Path,
) -> None:
    """每次运行生成新 run_id：重复运行不触发 analysis_run.run_id UNIQUE 冲突。"""
    use_case = RunAnalysisUseCase(fake_provider, store, golden_input)
    first = use_case.run()
    second = use_case.run()

    assert first.run_id != second.run_id
    assert len(store.list_runs()) == 2


def test_validation_failure_returns_issues_without_analyze(
    fake_provider: FakeEngineProvider,
    store: SqlResultStore,
    blocking_input: Path,
) -> None:
    """校验失败：返回错误列表、analyze 未被调用、无落库。"""
    counting = _CountingProvider(fake_provider)
    outcome = RunAnalysisUseCase(counting, store, blocking_input).run()

    assert isinstance(outcome, AnalysisOutcome)
    assert not outcome.ok
    assert outcome.result is None
    assert [issue.code.value for issue in outcome.issues] == ["DUPLICATE_EVENT"]
    assert counting.analyze_calls == 0
    assert store.list_runs() == []


def test_create_engine_provider_reads_env_once(monkeypatch) -> None:
    """组合根分支：缺省与 fake 返回 FakeEngineProvider，local 返回 LocalEngineProvider。"""
    monkeypatch.delenv("WORKBENCH_ENGINE", raising=False)
    assert isinstance(create_engine_provider(), FakeEngineProvider)

    monkeypatch.setenv("WORKBENCH_ENGINE", "fake")
    assert isinstance(create_engine_provider(), FakeEngineProvider)

    monkeypatch.setenv("WORKBENCH_ENGINE", "local")
    assert isinstance(create_engine_provider(), LocalEngineProvider)
