"""数据集提供方端口：分析用例的数据来源抽象（M1 Task 7.2）。

M0 数据源固定为 golden fixture；M1 起支持两种实现，选择逻辑只在组合根
（``app.main``），presentation / application 仅依赖本 Protocol：

- 本地库适配器（M1 默认）：``infrastructure.db.dataset_adapter.SqliteDatasetAdapter``
  ——从本地 SQLite 的 sku / inventory_event / stock_snapshot 构造数据集；
- golden fixture 提供方（测试/演示保留）：
  ``application.analysis_usecase.FixtureDatasetProvider``——M0 行为原样。
"""

from __future__ import annotations

from typing import Protocol

from contracts import AnalysisRequest, EngineDataset


class DatasetProvider(Protocol):
    """分析数据集提供方：构造分析请求与对应数据集。"""

    def load(self) -> tuple[AnalysisRequest, EngineDataset]:
        """返回（分析请求，数据集）。

        - 请求期间与仓库范围由提供方决定（本地实现取自库内数据范围）；
        - 空库 / 无数据时返回各列表为空的数据集，不抛异常；
        - run_id 为占位值，由用例统一覆盖为本次运行的新 ID。
        """
        ...
