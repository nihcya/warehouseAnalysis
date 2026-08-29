"""0001_meta：本地库元信息表（主基线 §35.7 本地迁移第 1 步）。

创建 local_meta 并写入种子：
- ``db_schema_version=local-0001``：本地库 schema 版本（迁移推进时同步更新）；
- ``install_instance_id=<uuid>``：安装实例标识（首次建库生成一次）；
- ``single_primary_workbench=1``：单主工作台标识（§35.3 单写入进程原则）。

M0 只建 meta 表；analysis_run / analysis_result 由 0002_analysis_m0
占位创建（主基线完整字段在 0005_analysis 落地，保持“只加不改”）。

回滚：drop local_meta（种子数据随之消失，downgrade 后重新 upgrade 会生成新实例 UUID）。
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_meta"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

#: 本地库 schema 版本（与 revision 同步推进）
SCHEMA_VERSION = "local-0001"

# 迁移内用轻量 table 引用，不 import ORM 模型（避免与模型演化耦合）
_local_meta = sa.table(
    "local_meta",
    sa.column("key", sa.Text),
    sa.column("value", sa.Text),
)


def upgrade() -> None:
    op.create_table(
        "local_meta",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_local_meta"),
    )
    op.execute(_local_meta.insert().values(key="db_schema_version", value=SCHEMA_VERSION))
    op.execute(
        _local_meta.insert().values(
            key="install_instance_id",
            value=str(uuid.uuid4()),
        )
    )
    op.execute(_local_meta.insert().values(key="single_primary_workbench", value="1"))


def downgrade() -> None:
    op.drop_table("local_meta")
