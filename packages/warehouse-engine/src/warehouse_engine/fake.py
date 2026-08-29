"""FakeEngine：fixture 驱动的联调引擎（M0）。

与 :class:`~warehouse_engine.engine.WarehouseEngine` 同接口，供 A 侧工作台在真实
计算器交付前联调（docs/开发规划与协作需求文档.md §30.4，fixture 路径
``tests/fixtures/fake-analysis.json``）：

- ``validate_dataset`` 委托真实校验规则（复用 validation/rules.py）；
- ``analyze`` 返回 fixture 冻结的 ``AnalysisResult``，仅把 ``run_id``、
  ``period_start``、``period_end`` 覆盖为 request 的值，其余字段原样返回；
  阻断数据与真实引擎一致抛 :class:`DataValidationError`；
- ``list_capabilities`` 与 WarehouseEngine 完全一致；
- progress 回调仅调用一次（完成信号），不改变计算结果。
"""

from __future__ import annotations

import json
from pathlib import Path

from warehouse_engine.__version__ import FORMULA_VERSION
from warehouse_engine.contracts import (
    AnalysisRequest,
    AnalysisResult,
    CapabilityDescriptor,
    EngineDataset,
    ValidationReport,
)
from warehouse_engine.engine import ProgressCallback, WarehouseEngine
from warehouse_engine.errors import DataValidationError
from warehouse_engine.validation.rules import apply_dataset_rules

#: FakeEngine 的引擎版本标识（区别于真实引擎，便于 UI 与测试识别联调来源）
FAKE_ENGINE_VERSION = "0.1.0-fake"


class FakeEngine:
    """fixture 驱动的假引擎：接口与 WarehouseEngine 一致，输出为冻结 fixture。"""

    engine_version: str = FAKE_ENGINE_VERSION
    formula_version: str = FORMULA_VERSION

    def __init__(self, fixture_result: AnalysisResult) -> None:
        self._fixture_result = fixture_result

    @classmethod
    def from_fixture(cls, path: Path) -> FakeEngine:
        """从 AnalysisResult 结构的 JSON 文件构建假引擎，结果缓存于实例。"""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(fixture_result=AnalysisResult.model_validate(payload))

    def validate_dataset(
        self,
        request: AnalysisRequest,
        dataset: EngineDataset,
    ) -> ValidationReport:
        """与 WarehouseEngine 相同的真实校验规则（复用 validation/rules.py）。"""
        issues, warnings = apply_dataset_rules(dataset, request)
        return ValidationReport(valid=not issues, issues=issues, warnings=warnings)

    def analyze(
        self,
        request: AnalysisRequest,
        dataset: EngineDataset,
        progress: ProgressCallback | None = None,
    ) -> AnalysisResult:
        """返回 fixture 结果，run_id 与分析期间覆盖为 request 的值，其余字段原样。

        阻断数据与真实引擎行为一致：抛 :class:`DataValidationError`；
        progress 回调仅调用一次（完成信号）。
        """
        report = self.validate_dataset(request, dataset)
        if not report.valid:
            raise DataValidationError(
                "输入数据校验未通过，分析已阻断。",
                details=[issue.model_dump(mode="json") for issue in report.issues],
            )
        result = self._fixture_result.model_copy(
            update={
                "run_id": request.run_id,
                "period_start": request.start_date,
                "period_end": request.end_date,
            }
        )
        if progress is not None:
            # FakeEngine 无真实计算阶段，仅调用一次完成信号
            progress(1.0)
        return result

    def list_capabilities(self) -> list[CapabilityDescriptor]:
        """能力清单与 WarehouseEngine 完全一致。"""
        return WarehouseEngine().list_capabilities()
