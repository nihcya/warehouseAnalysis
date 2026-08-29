"""ResultStore 端口的 local-data 实现（§7.6）。

SQL 唯一入口在 ``local_data.repository.AnalysisRepository``；
本类只做端口适配（save/get/list_runs），结果按 ``AnalysisResult`` 原样往返。
"""

from __future__ import annotations

from typing import Any

from contracts import AnalysisResult
from local_data.repository import AnalysisRepository
from sqlalchemy.orm import Session, sessionmaker


class SqlResultStore:
    """本地 SQLite 结果存储：包装 local-data 仓储，满足 domain 的 ResultStore 端口。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._repository = AnalysisRepository(session_factory)

    def save(self, result: AnalysisResult) -> str:
        """结果原样落库（SUCCEEDED 运行 + full_result 单行），返回 run_id。"""
        return self._repository.save_result(result)

    def get(self, run_id: str) -> AnalysisResult | None:
        """按 run_id 取回完整结果；不存在返回 None。"""
        return self._repository.get_result(run_id)

    def list_runs(self) -> list[dict[str, Any]]:
        """最近运行摘要列表（新 → 旧）。"""
        return self._repository.list_runs()
