"""分析用例（§7.4 + M1 Task 7.2）：加载数据 → 校验 → 计算 → 落库。

- 数据源参数化：组合根注入 ``DatasetProvider``——本地库适配器
  （``infrastructure.db.dataset_adapter.SqliteDatasetAdapter``）为 M1 默认，
  golden fixture 提供方保留供测试/演示；未注入 provider 时回退 M0 的
  golden 输入（``input_path``），加载方式与 tests/engine 一致；
- 每次运行生成新的 run_id（覆盖 provider 内的占位/固定值），
  避免重复运行触发 ``analysis_run.run_id`` UNIQUE 冲突；
- 无事实数据（空库或仅导入主数据，无流水与快照）时返回 ``no_data=True``
  的明确结果：不校验、不计算、不落库、不抛异常（UI 展示提示）；
- 校验失败（``valid=False``）时返回错误列表并立即结束，绝不调用 analyze。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from contracts import AnalysisRequest, AnalysisResult, EngineDataset, ValidationIssue

from ..domain.dataset_provider import DatasetProvider
from ..domain.engine_provider import EngineProvider, ProgressCallback
from ..domain.result_store import ResultStore

#: 仓库根（golden 输入等 fixture 的定位基准）
REPO_ROOT = Path(__file__).resolve().parents[4]

#: M0 默认输入：golden 输入（M1 起仅作 fixture 数据源，本地库为默认数据源）
DEFAULT_INPUT_PATH = REPO_ROOT / "tests" / "fixtures" / "golden" / "v0.1.0" / "input.json"


def load_analysis_inputs(path: Path) -> tuple[AnalysisRequest, EngineDataset]:
    """读取 ``{"request", "dataset"}`` 结构的 JSON，构造契约模型。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return (
        AnalysisRequest.model_validate(payload["request"]),
        EngineDataset.model_validate(payload["dataset"]),
    )


class FixtureDatasetProvider:
    """golden fixture 数据集提供方（M0 行为，测试/演示保留）。

    满足 domain 的 ``DatasetProvider`` 端口；请求与数据集来自同一份
    ``{"request": ..., "dataset": ...}`` JSON 输入文件。
    """

    def __init__(self, input_path: Path = DEFAULT_INPUT_PATH) -> None:
        self._input_path = Path(input_path)

    def load(self) -> tuple[AnalysisRequest, EngineDataset]:
        """读取 fixture 输入，返回（分析请求，数据集）。"""
        return load_analysis_inputs(self._input_path)


@dataclass
class AnalysisOutcome:
    """一次分析运行的产出。

    - 成功时 ``result`` 非空；
    - 校验失败时 ``issues`` 为错误列表；
    - 无事实数据（空库或仅导入主数据）时 ``no_data=True``，其余字段为空。
    """

    run_id: str
    result: AnalysisResult | None = None
    issues: list[ValidationIssue] = field(default_factory=list)
    no_data: bool = False

    @property
    def ok(self) -> bool:
        """校验通过且已完成计算并落库。"""
        return self.result is not None


class RunAnalysisUseCase:
    """分析用例：校验通过才计算，结果经 ResultStore 原样落库。"""

    def __init__(
        self,
        engine: EngineProvider,
        store: ResultStore,
        input_path: Path = DEFAULT_INPUT_PATH,
        dataset_provider: DatasetProvider | None = None,
    ) -> None:
        self._engine = engine
        self._store = store
        # 数据源选择逻辑只在组合根：注入 dataset provider（本地适配器为 M1
        # 默认）；未注入时回退 M0 的 golden fixture（input_path），测试/演示保留
        self._provider: DatasetProvider = (
            dataset_provider
            if dataset_provider is not None
            else FixtureDatasetProvider(input_path)
        )

    def run(self, progress: ProgressCallback | None = None) -> AnalysisOutcome:
        """执行一次分析：加载数据 → 校验 → 计算（progress 驱动）→ 保存。"""
        request, dataset = self._provider.load()
        # 每次运行生成新 run_id（run_id 语义为"一次运行"），保证可重复运行不撞 UNIQUE
        request = request.model_copy(update={"run_id": f"run-{uuid.uuid4().hex[:12]}"})
        # 无事实数据：不校验、不计算、不落库，返回明确的"无数据"结果（UI 展示提示）
        if not (dataset.movements or dataset.snapshots):
            return AnalysisOutcome(run_id=request.run_id, no_data=True)
        report = self._engine.validate_dataset(request, dataset)
        if not report.valid:
            return AnalysisOutcome(run_id=request.run_id, issues=list(report.issues))
        result = self._engine.analyze(request, dataset, progress)
        self._store.save(result)
        return AnalysisOutcome(run_id=result.run_id, result=result)
