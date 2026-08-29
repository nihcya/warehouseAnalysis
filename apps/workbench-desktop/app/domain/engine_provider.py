"""引擎提供方端口：应用层与展示层依赖的引擎抽象。

presentation / application 只依赖本 Protocol；fake / local 的选择由组合根
（``app.main`` / ``app.application.bootstrap``）完成，展示层不出现引擎分支。
接口与 ``warehouse_engine.engine.WarehouseEngine`` 公开行为保持同构。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from contracts import AnalysisRequest, AnalysisResult, EngineDataset, ValidationReport

#: 进度回调：接收 0.0~1.0 的完成比例（与 warehouse_engine.engine.ProgressCallback 同构）
ProgressCallback = Callable[[float], None]


class EngineProvider(Protocol):
    """分析引擎提供方：校验与计算的最小接口。"""

    @property
    def engine_version(self) -> str:
        """引擎版本标识（用于结果展示与落库追溯）。"""
        ...

    @property
    def formula_version(self) -> str:
        """公式版本标识（用于结果展示与落库追溯）。"""
        ...

    def validate_dataset(
        self,
        request: AnalysisRequest,
        dataset: EngineDataset,
    ) -> ValidationReport:
        """校验数据集，返回结构化报告；存在阻断 issue 时 valid=False。"""
        ...

    def analyze(
        self,
        request: AnalysisRequest,
        dataset: EngineDataset,
        progress: ProgressCallback | None = None,
    ) -> AnalysisResult:
        """执行分析；输入被阻断时应抛 DataValidationError（由调用方防御）。"""
        ...
