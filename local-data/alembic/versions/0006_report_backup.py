"""0006_report_backup：报告与备份 2 表（主基线 §35.4 分析和治理组
report_artifact / backup_record 两行，spec M1 Task 8/9）。

建表（字段与约束严格对齐 §35.4）：
- report_artifact：report_id 唯一标识一次导出产物；(run_id, format) UNIQUE
  （同一 run 同一格式只保留一条记录，重复导出走更新路径）；
  FK→analysis_run.run_id（报告与 run 一一对应可追溯）；
- backup_record：backup_id UNIQUE；backup_type 枚举 CHECK（MANUAL/AUTO）；
  status 枚举 CHECK（CREATED/VERIFIED/FAILED）；备份成功必须有校验值和验证时间
  （§35.4：verified_at 在 VERIFIED 时由应用层填写）；
  db_schema_version 记录备份时的本地库 schema 版本（恢复校验依据）。

M0 约定沿用：时间列 UTC ISO 8601 文本；每表 created_at/updated_at（§35.3）。

回滚：反序 drop 两表，db_schema_version 回退 local-0005。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_report_backup"
down_revision: str | None = "0005_import"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

#: 本地库 schema 版本（与 revision 同步推进）
SCHEMA_VERSION = "local-0006"

_local_meta = sa.table(
    "local_meta",
    sa.column("key", sa.Text),
    sa.column("value", sa.Text),
)


def upgrade() -> None:
    op.create_table(
        "report_artifact",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_report_artifact"),
        sa.UniqueConstraint("report_id", name="uq_report_artifact_report_id"),
        # 同一 run 同一格式只登记一条产物记录：重复导出 = 重新生成并更新记录
        sa.UniqueConstraint("run_id", "format", name="uq_report_artifact_run_id_format"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_run.run_id"],
            name="fk_report_artifact_run_id_analysis_run",
        ),
    )
    op.create_table(
        "backup_record",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("backup_id", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("backup_type", sa.Text(), nullable=False),
        sa.Column("db_schema_version", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("verified_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_backup_record"),
        sa.UniqueConstraint("backup_id", name="uq_backup_record_backup_id"),
        # 备份类型枚举：手动备份 / 自动备份（每日自动、恢复前安全备份等）
        sa.CheckConstraint(
            "backup_type IN ('MANUAL', 'AUTO')",
            name="ck_backup_record_backup_type",
        ),
        # 备份状态机：CREATED（文件已生成）→ VERIFIED（读回复验通过）/ FAILED
        sa.CheckConstraint(
            "status IN ('CREATED', 'VERIFIED', 'FAILED')",
            name="ck_backup_record_status",
        ),
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
        .values(value="local-0005")
    )
    op.drop_table("backup_record")
    op.drop_table("report_artifact")
