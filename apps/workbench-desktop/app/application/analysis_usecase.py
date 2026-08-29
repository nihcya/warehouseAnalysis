"""分析用例（§7.4）：加载输入 → 校验 → 计算 → 落库。

- M0 输入源固定为 golden 输入（``tests/fixtures/golden/v0.1.0/input.json``，
  结构 ``{"request": ..., "dataset": ...}``，加载方式与 tests/engine 一致）；
- 每次运行生成新的 run_id（覆盖 fixture 内固定值），
  避免重复运行触发 ``analysis_run.run_id`` UNIQUE 冲突；
- 校验失败（``valid=False``）时返回错误列表并立即结束，绝不调用 analyze。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from contracts import AnalysisRequest, AnalysisResult, EngineDataset, ValidationIssue

from ..domain.engine_provider import EngineProvider, ProgressCallback
from ..domain.result_store import ResultStore

#: 仓库根（golden 输入等 fixture 的定位基准）
REPO_ROOT = Path(__file__).resolve().parents[4]

#: M0 默认输入：golden 输入（真实数据导入功能 M1 交付）
DEFAULT_INPUT_PATH = REPO_ROOT / "tests" / "fixtures" / "golden" / "v0.1.0" / "input.json"


def load_analysis_inputs(path: Path) -> tuple[AnalysisRequest, EngineDataset]:
    """读取 ``{"request", "dataset"}`` 结构的 JSON，构造契约模型。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return (
        AnalysisRequest.model_validate(payload["request"]),
        EngineDataset.model_validate(payload["dataset"]),
    )


@dataclass
class AnalysisOutcome:
    """一次分析运行的产出：成功时 ``result`` 非空，校验失败时 ``issues`` 为错误列表。"""

    run_id: str
    result: AnalysisResult | None = None
    issues: list[ValidationIssue] = field(default_factory=list)

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
    ) -> None:
        self._engine = engine
        self._store = store
        self._input_path = Path(input_path)

    def run(self, progress: ProgressCallback | None = None) -> AnalysisOutcome:
        """执行一次分析：校验 → 计算（progress 驱动）→ 保存。"""
        request, dataset = load_analysis_inputs(self._input_path)
        # 每次运行生成新 run_id（run_id 语义为"一次运行"），保证可重复运行不撞 UNIQUE
        request = request.model_copy(update={"run_id": f"run-{uuid.uuid4().hex[:12]}"})
        report = self._engine.validate_dataset(request, dataset)
        if not report.valid:
            return AnalysisOutcome(run_id=request.run_id, issues=list(report.issues))
        result = self._engine.analyze(request, dataset, progress)
        self._store.save(result)
        return AnalysisOutcome(run_id=result.run_id, result=result)
