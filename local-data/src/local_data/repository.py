"""分析结果仓储：本地库 analysis_run / analysis_result 的唯一 SQL 入口。

主基线 §35.3：数据库访问统一通过 Repository 与事务服务；
UI、Skill、报告模板一律不得拼接 SQL。

M0 语义：save_result 以“SUCCEEDED 运行 + full_result 单行”落库，
get_result 按 run_id 用 AnalysisResult.model_validate_json 还原（往返一致）。
"""

from __future__ import annotations

import json
from typing import Any

from contracts import AnalysisResult
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from local_data.models import (
    RUN_STATUS_SUCCEEDED,
    AnalysisResultRow,
    AnalysisRun,
    utc_now_iso,
)

#: M0 完整结果行的 result_type 约定（M1 拆分指标行时保留该类型以兼容）
FULL_RESULT_TYPE = "full_result"


class AnalysisRepository:
    """analysis_run / analysis_result 仓储。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_result(self, result: AnalysisResult, task_id: str | None = None) -> str:
        """保存完整分析结果，返回 run_id。

        - 单事务写入：analysis_run（SUCCEEDED）+ analysis_result 单行；
        - metric_json 存 ``result.model_dump_json()`` 原样序列化（金额为字符串，禁止 float 真值）；
        - warning_json 存 warnings 的 JSON 数组（与 metric_json 内 warnings 一致，便于直查）；
        - run_id 重复保存会触发 UNIQUE 冲突（IntegrityError），覆盖策略由调用方决定。
        """
        run_id = result.run_id
        now = utc_now_iso()
        run = AnalysisRun(
            run_id=run_id,
            task_id=task_id,
            start_date=result.period_start.isoformat(),
            end_date=result.period_end.isoformat(),
            scope_json=None,  # AnalysisResult 不含仓库范围，M0 置空
            engine_version=result.engine_version,
            formula_version=result.formula_version,
            status=RUN_STATUS_SUCCEEDED,
            started_at=now,
            finished_at=now,
            created_at=now,
            updated_at=now,
        )
        row = AnalysisResultRow(
            run_id=run_id,
            result_type=FULL_RESULT_TYPE,
            metric_json=result.model_dump_json(),
            warning_json=json.dumps(
                [warning.model_dump(mode="json") for warning in result.warnings],
                ensure_ascii=False,
            ),
            created_at=now,
        )
        with self._session_factory() as session, session.begin():
            session.add(run)
            session.add(row)
        return run_id

    def get_result(self, run_id: str) -> AnalysisResult | None:
        """按 run_id 取回完整结果；不存在或无 full_result 行时返回 None。"""
        with self._session_factory() as session:
            row = session.execute(
                select(AnalysisResultRow).where(
                    AnalysisResultRow.run_id == run_id,
                    AnalysisResultRow.result_type == FULL_RESULT_TYPE,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return AnalysisResult.model_validate_json(row.metric_json)

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """最近运行的摘要列表（按创建时间倒序，默认最多 50 条）。"""
        statement = (
            select(AnalysisRun)
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
            .limit(limit)
        )
        with self._session_factory() as session:
            runs = session.execute(statement).scalars().all()
            return [
                {
                    "run_id": run.run_id,
                    "task_id": run.task_id,
                    "start_date": run.start_date,
                    "end_date": run.end_date,
                    "engine_version": run.engine_version,
                    "formula_version": run.formula_version,
                    "status": run.status,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "error_code": run.error_code,
                    "created_at": run.created_at,
                }
                for run in runs
            ]
