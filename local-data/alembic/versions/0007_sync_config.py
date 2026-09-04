"""0007_sync_config：小程序事件同步 2 表（M3 Task 4）。

建表（spec M3 事件同步链路：云端密文中继，本地收件 / 确认）：
- sync_inbox：云端拉取的加密信封收件箱。event_id 全局唯一主键
  （幂等落库：重复拉取 / 重复投递不产生第二行）；envelope_ciphertext 原样
  保存密文（本地侧解密，密钥经环境变量注入不入库）；applied_at 非空即已
  应用，apply_error / error_code 记录解密（DECRYPT_FAILED）或应用
  （APPLY_FAILED）失败原因，失败保留密文待重试 / 排查；
- sync_outbox：出站 ACK 登记。ack_id（云端 envelope_id）唯一主键（幂等
  确认）；本地落库成功才登记（落库成功为准），status 状态机 PENDING →
  ACKED（云端确认成功后置位），PENDING 记录由 SyncWorker 下一轮重发
  （ACK 失败不重写落库）。

M0 约定沿用：时间列 UTC ISO 8601 文本。

回滚：反序 drop 两表，db_schema_version 回退 local-0006。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_sync_config"
down_revision: str | None = "0006_report_backup"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

#: 本地库 schema 版本（与 revision 同步推进）
SCHEMA_VERSION = "local-0007"

_local_meta = sa.table(
    "local_meta",
    sa.column("key", sa.Text),
    sa.column("value", sa.Text),
)


def upgrade() -> None:
    op.create_table(
        "sync_inbox",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("envelope_ciphertext", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("received_at", sa.Text(), nullable=False),
        sa.Column("applied_at", sa.Text(), nullable=True),
        sa.Column("apply_error", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("event_id", name="pk_sync_inbox"),
    )
    op.create_table(
        "sync_outbox",
        sa.Column("ack_id", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("acked_at", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("ack_id", name="pk_sync_outbox"),
        # ACK 与收件信封一一对应：event_id 必须已在 sync_inbox 登记
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["sync_inbox.event_id"],
            name="fk_sync_outbox_event_id_sync_inbox",
        ),
        # ACK 状态机：PENDING（待云端确认）→ ACKED（确认成功，终态）
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACKED')",
            name="ck_sync_outbox_status",
        ),
    )
    op.create_index("ix_sync_outbox_status", "sync_outbox", ["status"], unique=False)
    op.execute(
        sa.update(_local_meta)
        .where(_local_meta.c.key == "db_schema_version")
        .values(value=SCHEMA_VERSION)
    )


def downgrade() -> None:
    op.execute(
        sa.update(_local_meta)
        .where(_local_meta.c.key == "db_schema_version")
        .values(value="local-0006")
    )
    op.drop_index("ix_sync_outbox_status", table_name="sync_outbox")
    op.drop_table("sync_outbox")
    op.drop_table("sync_inbox")
