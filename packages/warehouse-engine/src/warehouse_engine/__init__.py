"""warehouse-engine：仓库品类分析引擎（M0 基线骨架）。

公开入口：WarehouseEngine、FakeEngine（fixture 驱动、供 A 侧工作台联调）
与统一异常体系。
"""

from warehouse_engine.__version__ import ENGINE_VERSION, FORMULA_VERSION
from warehouse_engine.engine import WarehouseEngine
from warehouse_engine.errors import AnalysisCancelledError, DataValidationError, EngineError
from warehouse_engine.fake import FAKE_ENGINE_VERSION, FakeEngine

__all__ = [
    "ENGINE_VERSION",
    "FAKE_ENGINE_VERSION",
    "FORMULA_VERSION",
    "AnalysisCancelledError",
    "DataValidationError",
    "EngineError",
    "FakeEngine",
    "WarehouseEngine",
]
