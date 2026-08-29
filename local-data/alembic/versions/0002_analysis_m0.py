"""0002_analysis_m0：M0 分析持久化占位（analysis_run / analysis_result）。

主基线 §35.7 规定分析表属于 0005_analysis 迁移；M0 为满足
“AnalysisResult 原样存取”提前以本迁移建表。0002 为 M0 持久化占位，
M1 落 0005 完整字段时保持兼容（只加不改：仅新增字段/索引，不修改既有列）。

字段对齐主基线 §35.4：
- analysis_run：run_id UNIQUE、engine/formula 版本、状态机、时间与错误码；
- analysis_result：FK→analysis_run.run_id、(run_id, result_type, sku_id) 索引。

金额/数量以 TEXT 存 Decimal 序列化字符串（M0 约定，禁止 float 真值）。

回滚：删除索引与两张表，db_schema_version 回退 local-0001。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_analysis_m0"
down_revision: str | None = "0001_meta"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

#: 本地库 schema 版本（与 revision 同步推进）
SCHEMA_VERSION = "local-0002"

_local_meta = sa.table(
    "local_meta",
    sa.column("key", sa.Text),
    sa.column("value", sa.Text),
)


def upgrade() -> None:
    op.create_table(
        "analysis_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Text(), nullable=True),
        sa.Column("end_date", sa.Text(), nullable=True),
        sa.Column("scope_json", sa.Text(), nullable=True),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column("formula_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_analysis_run"),
        sa.UniqueConstraint("run_id", name="uq_analysis_run_run_id"),
    )
    op.create_table(
        "analysis_result",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("result_type", sa.Text(), nullable=False),
        sa.Column("sku_id", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("metric_json", sa.Text(), nullable=False),
        sa.Column("warning_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_analysis_result"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_run.run_id"],
            name="fk_analysis_result_run_id_analysis_run",
        ),
    )
    op.create_index(
        "ix_analysis_result_run_id_result_type_sku_id",
        "analysis_result",
        ["run_id", "result_type", "sku_id"],
        unique=False,
    )
    op.execute(
        sa.update(_local_meta)
        .where(_local_meta.c.key == "db_schema_version")
        .values(value=SCHEMA_VERSION)
    )


def downgrade() -> None:
    op.execute(
        sa.update(_local_meta)
        .where(_local_meta.c.key == "db_schema_version")
        .values(value="local-0001")
    )
    op.drop_index(
        "ix_analysis_result_run_id_result_type_sku_id",
        table_name="analysis_result",
    )
    op.drop_table("analysis_result")
    op.drop_table("analysis_run")
