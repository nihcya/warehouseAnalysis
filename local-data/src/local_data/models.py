"""本地业务库 ORM 模型（SQLAlchemy 2.0 declarative）。

- 表结构对齐主基线 §35.4；DDL 一律由 Alembic 迁移创建（0001_meta、0002_analysis_m0）。
- 时间字段统一存 UTC ISO 8601 文本；日期字段存 YYYY-MM-DD 文本。
- 金额/数量不使用 float 真值：M0 约定模型层用 TEXT 存 Decimal 序列化字符串
  （AnalysisResult 的 JSON 序列化中金额已是字符串，见 contracts.analysis 序列化约定）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, Index, MetaData, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 文本（本地库时间统一为 UTC 文本）。"""
    return datetime.now(UTC).isoformat()


#: 本地库约束命名约定：约束显式命名，保证错误可定位、迁移可回滚
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: analysis_run.status 状态机取值（M0 冻结；新增状态须走“只加不改”迁移）
RUN_STATUS_CREATED = "CREATED"
RUN_STATUS_QUEUED = "QUEUED"
RUN_STATUS_RUNNING = "RUNNING"
RUN_STATUS_SUCCEEDED = "SUCCEEDED"
RUN_STATUS_FAILED = "FAILED"
RUN_STATUS_CANCELLED = "CANCELLED"

#: 全部合法状态（Repository 写入使用，UI 只读展示）
RUN_STATUSES: tuple[str, ...] = (
    RUN_STATUS_CREATED,
    RUN_STATUS_QUEUED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCEEDED,
    RUN_STATUS_FAILED,
    RUN_STATUS_CANCELLED,
)


class Base(DeclarativeBase):
    """本地库 declarative 基类（metadata 携带命名约定）。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class AnalysisRun(Base):
    """分析运行（analysis_run）：run_id 唯一，记录引擎/公式版本与状态机。"""

    __tablename__ = "analysis_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    task_id: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[str | None] = mapped_column(Text)  # YYYY-MM-DD
    end_date: Mapped[str | None] = mapped_column(Text)  # YYYY-MM-DD
    scope_json: Mapped[str | None] = mapped_column(Text)  # 仓库范围等 scope 快照
    engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    formula_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str | None] = mapped_column(Text)  # UTC ISO 8601
    finished_at: Mapped[str | None] = mapped_column(Text)  # UTC ISO 8601
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso, onupdate=utc_now_iso)

    # 双向关系：让单元工作流知道 run 先于 result 插入（FK 顺序），不改变表结构
    results: Mapped[list[AnalysisResultRow]] = relationship(back_populates="run")


class AnalysisResultRow(Base):
    """分析结果行（analysis_result）：(run_id, result_type, sku_id) 复合索引。

    M0 约定：result_type="full_result" 单行存 AnalysisResult 完整 JSON；
    metric_json 中金额/数量为字符串文本（Decimal 序列化，禁止 float 真值）。
    """

    __tablename__ = "analysis_result"
    __table_args__ = (
        Index("ix_analysis_result_run_id_result_type_sku_id", "run_id", "result_type", "sku_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(Text, ForeignKey("analysis_run.run_id"), nullable=False)
    result_type: Mapped[str] = mapped_column(Text, nullable=False)
    sku_id: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    metric_json: Mapped[str] = mapped_column(Text, nullable=False)
    warning_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)

    run: Mapped[AnalysisRun] = relationship(back_populates="results")


class MetaInfo(Base):
    """本地元信息（local_meta）：db_schema_version、install_instance_id、单主工作台标识。"""

    __tablename__ = "local_meta"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
