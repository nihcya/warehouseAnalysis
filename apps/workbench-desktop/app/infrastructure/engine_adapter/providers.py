"""引擎提供方实现（§7.3）：FakeEngine（fixture 冻结联调）与本地 WarehouseEngine。

两者的选择只发生在组合根（``app.main``，环境变量 ``WORKBENCH_ENGINE``），
本模块不做任何环境读取；UI 与用例仅依赖 domain 的 ``EngineProvider`` 端口。
"""

from __future__ import annotations

from pathlib import Path

from contracts import AnalysisRequest, AnalysisResult, EngineDataset, ValidationReport
from warehouse_engine import FakeEngine, WarehouseEngine

from ...domain.engine_provider import ProgressCallback


class FakeEngineProvider:
    """联调引擎提供方：包装 ``warehouse_engine.fake.FakeEngine``。

    结果来自冻结 fixture（``tests/fixtures/fake-analysis.json``），
    校验规则与真实引擎一致（复用 validation/rules.py）。
    """

    def __init__(self, fixture_path: Path) -> None:
        self._engine = FakeEngine.from_fixture(Path(fixture_path))

    @property
    def engine_version(self) -> str:
        """FakeEngine 版本标识（0.1.0-fake），供 UI 与落库追溯联调来源。"""
        return self._engine.engine_version

    @property
    def formula_version(self) -> str:
        """公式版本标识（与真实引擎一致）。"""
        return self._engine.formula_version

    def validate_dataset(
        self,
        request: AnalysisRequest,
        dataset: EngineDataset,
    ) -> ValidationReport:
        """委托 FakeEngine：与 WarehouseEngine 相同的真实校验规则。"""
        return self._engine.validate_dataset(request, dataset)

    def analyze(
        self,
        request: AnalysisRequest,
        dataset: EngineDataset,
        progress: ProgressCallback | None = None,
    ) -> AnalysisResult:
        """委托 FakeEngine：返回 fixture 冻结结果，progress 仅一次完成信号。"""
        return self._engine.analyze(request, dataset, progress)


class LocalEngineProvider:
    """本地引擎提供方：包装 ``warehouse_engine.engine.WarehouseEngine``（真实计算）。"""

    def __init__(self) -> None:
        self._engine = WarehouseEngine()

    @property
    def engine_version(self) -> str:
        """真实引擎版本标识。"""
        return self._engine.engine_version

    @property
    def formula_version(self) -> str:
        """公式版本标识。"""
        return self._engine.formula_version

    def validate_dataset(
        self,
        request: AnalysisRequest,
        dataset: EngineDataset,
    ) -> ValidationReport:
        """委托 WarehouseEngine 的基础校验。"""
        return self._engine.validate_dataset(request, dataset)

    def analyze(
        self,
        request: AnalysisRequest,
        dataset: EngineDataset,
        progress: ProgressCallback | None = None,
    ) -> AnalysisResult:
        """委托 WarehouseEngine 计算（M0 为占位结果，M1 起接入真实计算器）。"""
        return self._engine.analyze(request, dataset, progress)
