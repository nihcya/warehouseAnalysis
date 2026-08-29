"""0005_import：导入治理 2 表（主基线 §35.4 分析和治理组 import_* 两行）。

建表（字段与约束严格对齐 §35.4）：
- import_batch：batch_id UNIQUE；status 枚举 CHECK（RUNNING/COMPLETED/FAILED）；
  file_hash 建索引（重复文件导入提示据此检出，不静默重复入库）；
- import_error：FK→import_batch.batch_id；(batch_id, row_no) 索引定位错误行；
  raw_value / suggestion 支撑错误修复闭环（敏感值只落业务库，禁入技术日志）。

M0 约定沿用：时间列 UTC ISO 8601 文本；每表 created_at/updated_at（§35.3）。

回滚：反序 drop 两表，db_schema_version 回退 local-0004。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_import"
down_revision: str | None = "0004_inventory_events"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

#: 本地库 schema 版本（与 revision 同步推进）
SCHEMA_VERSION = "local-0005"

_local_meta = sa.table(
    "local_meta",
    sa.column("key", sa.Text),
    sa.column("value", sa.Text),
)


def upgrade() -> None:
    op.create_table(
        "import_batch",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("file_hash", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_import_batch"),
        sa.UniqueConstraint("batch_id", name="uq_import_batch_batch_id"),
        # 状态枚举：批次状态机 RUNNING → COMPLETED / FAILED，未知状态不允许落库
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED', 'FAILED')",
            name="ck_import_batch_status",
        ),
    )
    op.create_index("ix_import_batch_file_hash", "import_batch", ["file_hash"], unique=False)

    op.create_table(
        "import_error",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("row_no", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("suggestion", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_import_error"),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["import_batch.batch_id"],
            name="fk_import_error_batch_id_import_batch",
        ),
    )
    op.create_index(
        "ix_import_error_batch_id_row_no",
        "import_error",
        ["batch_id", "row_no"],
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
        .values(value="local-0004")
    )
    op.drop_index("ix_import_error_batch_id_row_no", table_name="import_error")
    op.drop_table("import_error")
    op.drop_index("ix_import_batch_file_hash", table_name="import_batch")
    op.drop_table("import_batch")
