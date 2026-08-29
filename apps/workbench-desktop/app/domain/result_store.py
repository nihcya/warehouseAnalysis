"""结果存储端口：分析结果的持久化与取回接口。

实现位于 infrastructure.db（包装 local-data 的 AnalysisRepository）；
application / presentation 只依赖本 Protocol，不触碰 SQL。
"""

from __future__ import annotations

from typing import Any, Protocol

from contracts import AnalysisResult


class ResultStore(Protocol):
    """分析结果存储：保存 / 按 run_id 取回 / 运行列表。"""

    def save(self, result: AnalysisResult) -> str:
        """结果原样落库，返回 run_id。"""
        ...

    def get(self, run_id: str) -> AnalysisResult | None:
        """按 run_id 取回完整结果；不存在返回 None。"""
        ...

    def list_runs(self) -> list[dict[str, Any]]:
        """最近运行摘要列表（新 → 旧）。"""
        ...
