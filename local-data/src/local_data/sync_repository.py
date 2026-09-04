"""小程序事件同步仓储（M3 Task 4）：sync_inbox / sync_outbox 的唯一 SQL 入口。

- record_envelope：信封落地（event_id 主键幂等，重复返回 False）；
- mark_applied / mark_failed：解密+校验+落库事务后的状态回写
  （DECRYPT_FAILED 保留密文，换钥后可重放）；
- record_ack / mark_acked：落库成功先记 PENDING，云端 ACK 成功后置 ACKED
  （云端 ACK 幂等，重复确认无副作用）；
- pending_count / list_pending_acks：待确认 ACK 视图，worker 每轮重发。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from local_data.models import (
    SYNC_ACK_STATUS_ACKED,
    SYNC_ACK_STATUS_PENDING,
    SyncInboxRow,
    SyncOutboxRow,
    utc_now_iso,
)


class SyncRepository:
    """同步收发件箱仓储：信封落地、应用状态回写与 ACK 流转。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record_envelope(self, event_id: str, ciphertext: str, source: str) -> bool:
        """幂等落地一个信封：event_id 已存在时跳过并返回 False，新插入返回 True。

        密文原样保留（解密失败不丢数据）；单主工作台单写入进程（§35.3），
        先查后插无并发竞争。
        """
        with self._session_factory() as session, session.begin():
            exists = session.execute(
                select(SyncInboxRow.event_id).where(SyncInboxRow.event_id == event_id)
            ).scalar_one_or_none()
            if exists is not None:
                return False
            session.add(
                SyncInboxRow(
                    event_id=event_id,
                    envelope_ciphertext=ciphertext,
                    source=source,
                    received_at=utc_now_iso(),
                )
            )
        return True

    def get_envelope(self, event_id: str) -> SyncInboxRow | None:
        """按 event_id 查询收件箱行（worker 重放与测试定位用）。"""
        with self._session_factory() as session:
            return session.execute(
                select(SyncInboxRow).where(SyncInboxRow.event_id == event_id)
            ).scalar_one_or_none()

    def mark_applied(self, event_id: str) -> bool:
        """标记事件已成功落库：置 applied_at 并清空失败信息；不存在返回 False。"""
        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(SyncInboxRow).where(SyncInboxRow.event_id == event_id)
            ).scalar_one_or_none()
            if row is None:
                return False
            row.applied_at = utc_now_iso()
            row.apply_error = None
            row.error_code = None
            return True

    def mark_failed(self, event_id: str, error_code: str, error: str) -> bool:
        """标记应用失败：记 error_code / apply_error，密文保留待重放；不存在返回 False。"""
        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(SyncInboxRow).where(SyncInboxRow.event_id == event_id)
            ).scalar_one_or_none()
            if row is None:
                return False
            row.error_code = error_code
            row.apply_error = error
            return True

    def record_ack(self, ack_id: str, event_id: str) -> bool:
        """幂等登记一条待确认 ACK（ack_id = 云端 envelope_id）：已存在返回 False。"""
        with self._session_factory() as session, session.begin():
            exists = session.execute(
                select(SyncOutboxRow.ack_id).where(SyncOutboxRow.ack_id == ack_id)
            ).scalar_one_or_none()
            if exists is not None:
                return False
            session.add(
                SyncOutboxRow(
                    ack_id=ack_id,
                    event_id=event_id,
                    acked_at=utc_now_iso(),
                    status=SYNC_ACK_STATUS_PENDING,
                )
            )
        return True

    def list_pending_acks(self) -> list[SyncOutboxRow]:
        """全部 PENDING 状态的 ACK（按登记先后排序，worker 每轮重发）。"""
        with self._session_factory() as session:
            return list(
                session.execute(
                    select(SyncOutboxRow)
                    .where(SyncOutboxRow.status == SYNC_ACK_STATUS_PENDING)
                    .order_by(SyncOutboxRow.acked_at, SyncOutboxRow.ack_id)
                ).scalars()
            )

    def mark_acked(self, ack_id: str) -> bool:
        """云端 ACK 确认成功：置 ACKED；不存在或已 ACKED 返回 False（幂等无害）。"""
        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(SyncOutboxRow).where(SyncOutboxRow.ack_id == ack_id)
            ).scalar_one_or_none()
            if row is None or row.status == SYNC_ACK_STATUS_ACKED:
                return False
            row.status = SYNC_ACK_STATUS_ACKED
            row.acked_at = utc_now_iso()
            return True

    def pending_count(self) -> int:
        """待云端确认的 ACK 数量（sync_outbox 中 PENDING 行数）。"""
        with self._session_factory() as session:
            return session.execute(
                select(func.count())
                .select_from(SyncOutboxRow)
                .where(SyncOutboxRow.status == SYNC_ACK_STATUS_PENDING)
            ).scalar_one()
