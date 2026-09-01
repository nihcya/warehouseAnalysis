"""基准数据注入链路测试（M2 Issue #10）。

引擎的 F-BM-001 **不读文件、不访问网络**，基准记录必须由调用方经
``AnalysisRequest.parameters["benchmarks"]`` 注入，另需 ``industry`` /
``region`` 用于匹配。本文件验证：

- JSON 提供方能正确载入版本化基准数据集并注入请求参数；
- 文件缺失 / 内容非法时**静默降级**为空参数（引擎发 BENCHMARK_UNAVAILABLE，
  不阻断分析主流程）；
- 用例把参数并入请求，且不覆盖请求自带的其他参数。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from contracts import AnalysisRequest, EngineDataset, ValidationReport
from workbench.application.analysis_usecase import RunAnalysisUseCase
from workbench.infrastructure.benchmark.benchmark_loader import (
    JsonBenchmarkProvider,
    NullBenchmarkProvider,
)
from workbench.infrastructure.db.dataset_adapter import SqliteDatasetAdapter
from workbench.infrastructure.engine_adapter.providers import LocalEngineProvider

#: 仓库根（tests → workbench-desktop → apps → 仓库根）
REPO_ROOT = Path(__file__).resolve().parents[3]

#: B 侧随引擎交付的版本化基准数据集
BENCHMARK_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "benchmarks" / "v0.1.0.json"

#: 基准记录必备字段（docs/formula-spec.md §9）
REQUIRED_RECORD_FIELDS = (
    "source",
    "region",
    "industry",
    "sample_scope",
    "updated_at",
    "benchmark_version",
    "unit",
    "applicability",
    "metric",
    "value",
)


class _SpyEngine:
    """引擎探针：记录用例实际传给引擎的 request（用于断言参数注入）。"""

    def __init__(self) -> None:
        self._inner = LocalEngineProvider()
        self.requests: list[AnalysisRequest] = []

    @property
    def engine_version(self) -> str:
        """真实引擎版本。"""
        return self._inner.engine_version

    @property
    def formula_version(self) -> str:
        """公式版本。"""
        return self._inner.formula_version

    def validate_dataset(
        self, request: AnalysisRequest, dataset: EngineDataset
    ) -> ValidationReport:
        """记录请求后委托真实引擎。"""
        self.requests.append(request)
        return self._inner.validate_dataset(request, dataset)

    def analyze(
        self,
        request: AnalysisRequest,
        dataset: EngineDataset,
        progress: Any = None,
    ) -> Any:
        """记录请求后委托真实引擎。"""
        self.requests.append(request)
        return self._inner.analyze(request, dataset, progress)

    @property
    def last_parameters(self) -> dict[str, Any]:
        """最后一次调用所用请求的参数字典。"""
        return dict(self.requests[-1].parameters)


#: fixture 内 B 侧故意放置的负向样本（source 含此标记，用于验证解析阶段丢弃）
NEGATIVE_SAMPLE_MARKER = "不应被使用"


def test_fixture_records_carry_required_fields() -> None:
    """B 侧交付的基准 fixture 可用；除故意的负向样本外，记录字段齐全。

    fixture 内含一条 ``metric`` 缺失的记录（source 标注「不应被使用」），
    由引擎 ``parse_benchmarks`` 在解析阶段丢弃；本提供方只负责原样注入，
    不做校验（校验口径归引擎，避免两处重复实现）。
    """
    provider = JsonBenchmarkProvider(BENCHMARK_FIXTURE, industry="综合零售", region="全国")
    records = provider.load_parameters()["benchmarks"]

    assert isinstance(records, list) and records
    negative = [r for r in records if NEGATIVE_SAMPLE_MARKER in str(r.get("source", ""))]
    complete = [r for r in records if NEGATIVE_SAMPLE_MARKER not in str(r.get("source", ""))]

    assert len(negative) == 1, "fixture 应恰好含一条故意的负向样本"
    assert complete, "fixture 应含可用的完整记录"
    for record in complete:
        missing = [key for key in REQUIRED_RECORD_FIELDS if key not in record]
        assert missing == [], f"基准记录缺字段 {missing}"


def test_negative_sample_is_injected_as_is() -> None:
    """提供方原样注入负向样本，丢弃逻辑交由引擎（单一职责）。"""
    provider = JsonBenchmarkProvider(BENCHMARK_FIXTURE, industry="综合零售", region="全国")
    records = provider.load_parameters()["benchmarks"]

    negative = [r for r in records if NEGATIVE_SAMPLE_MARKER in str(r.get("source", ""))]
    assert len(negative) == 1
    assert "metric" not in negative[0], "负向样本应保持缺 metric，由引擎丢弃"


def test_benchmark_version_exposed() -> None:
    """基准版本可被 UI/日志追溯。"""
    provider = JsonBenchmarkProvider(BENCHMARK_FIXTURE)
    assert provider.benchmark_version == "v0.1.0"


def test_missing_file_degrades_to_empty_parameters(tmp_path: Path) -> None:
    """文件缺失：返回空参数（引擎降级为 BENCHMARK_UNAVAILABLE），不抛异常。"""
    provider = JsonBenchmarkProvider(tmp_path / "not-exists.json")
    assert provider.load_parameters() == {}
    assert provider.benchmark_version == ""


def test_malformed_json_degrades_to_empty_parameters(tmp_path: Path) -> None:
    """内容非法（含非对象、records 非数组）：同样静默降级。"""
    broken = tmp_path / "broken.json"
    broken.write_text("{not-json", encoding="utf-8")
    assert JsonBenchmarkProvider(broken).load_parameters() == {}

    not_object = tmp_path / "list.json"
    not_object.write_text("[1, 2, 3]", encoding="utf-8")
    assert JsonBenchmarkProvider(not_object).load_parameters() == {}

    bad_records = tmp_path / "bad-records.json"
    bad_records.write_text('{"benchmark_version": "v0.1.0", "records": "x"}', encoding="utf-8")
    assert JsonBenchmarkProvider(bad_records).load_parameters() == {}


def test_null_provider_injects_nothing() -> None:
    """空实现：不注入任何参数。"""
    assert NullBenchmarkProvider().load_parameters() == {}
    assert NullBenchmarkProvider().benchmark_version == ""


def test_use_case_injects_benchmark_parameters(
    seeded_local_db: Any,
    store: Any,
) -> None:
    """端到端：基准参数随请求进入引擎（走本地库真实数据）。"""
    spy = _SpyEngine()
    provider = JsonBenchmarkProvider(BENCHMARK_FIXTURE, industry="综合零售", region="全国")
    use_case = RunAnalysisUseCase(
        spy,
        store,
        dataset_provider=SqliteDatasetAdapter(seeded_local_db),
        benchmark_provider=provider,
    )

    outcome = use_case.run()

    assert outcome.ok
    assert use_case.benchmark_version == "v0.1.0"
    parameters = spy.last_parameters
    assert parameters["industry"] == "综合零售"
    assert parameters["region"] == "全国"
    assert parameters["benchmarks"] == provider.load_parameters()["benchmarks"]


def test_use_case_without_benchmark_provider_injects_nothing(
    seeded_local_db: Any,
    store: Any,
) -> None:
    """未配置基准：参数为空，分析仍正常完成（引擎发 BENCHMARK_UNAVAILABLE）。"""
    spy = _SpyEngine()
    use_case = RunAnalysisUseCase(
        spy, store, dataset_provider=SqliteDatasetAdapter(seeded_local_db)
    )

    outcome = use_case.run()

    assert outcome.ok
    assert use_case.benchmark_version == ""
    assert spy.last_parameters == {}
