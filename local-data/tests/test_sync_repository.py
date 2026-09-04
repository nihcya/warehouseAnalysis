"""0007_sync_config 迁移与 SyncRepository 测试（M3 Task 4）。

- 迁移结构：sync_inbox / sync_outbox 建表、FK/CHECK 实际生效；
- SyncRepository：record_envelope / mark_applied / mark_failed / record_ack
  / mark_acked 的幂等语义与 pending_count 视图。
"""

from __future__ import annotations

import pytest
from local_data.models import (
    SYNC_ACK_STATUS_ACKED,
    SYNC_ACK_STATUS_PENDING,
    SYNC_ERROR_APPLY_FAILED,
    SYNC_ERROR_DECRYPT_FAILED,
    SyncOutboxRow,
)
from local_data.sync_repository import SyncRepository
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

CIPHERTEXT_A = "gAAAAABfake-ciphertext-a"
CIPHERTEXT_B = "gAAAAABfake-ciphertext-b"


# ---------------------------------------------------------------------------
# 迁移结构
# ---------------------------------------------------------------------------


def test_sync_tables_fk_enforced(session_factory: sessionmaker[Session]) -> None:
    """sync_outbox.event_id 外键实际生效：引用不存在的 inbox 行报 IntegrityError。"""
    repo = SyncRepository(session_factory)
    assert repo.record_envelope("evt-fk", CIPHERTEXT_A, "MINIAPP_SYNC")
    with pytest.raises(IntegrityError):
        repo.record_ack("env-fk", "evt-missing")


def test_sync_outbox_status_check_enforced(session_factory: sessionmaker[Session]) -> None:
    """迁移内 CHECK 约束实际生效：非法 status 直接被数据库拒绝。"""
    repo = SyncRepository(session_factory)
    repo.record_envelope("evt-check", CIPHERTEXT_A, "MINIAPP_SYNC")
    repo.record_ack("env-check", "evt-check")
    with pytest.raises(IntegrityError), session_factory() as session, session.begin():
        session.execute(
            text("UPDATE sync_outbox SET status = 'BOGUS' WHERE ack_id = 'env-check'")
        )


# ---------------------------------------------------------------------------
# record_envelope：幂等落地
# ---------------------------------------------------------------------------


def test_record_envelope_idempotent(session_factory: sessionmaker[Session]) -> None:
    """首次插入返回 True，重复 event_id 返回 False 且不覆盖原密文。"""
    repo = SyncRepository(session_factory)
    assert repo.record_envelope("evt-1", CIPHERTEXT_A, "MINIAPP_SYNC") is True
    assert repo.record_envelope("evt-1", CIPHERTEXT_B, "MINIAPP_SYNC") is False

    row = repo.get_envelope("evt-1")
    assert row is not None
    assert row.envelope_ciphertext == CIPHERTEXT_A  # 原密文保留，未被覆盖
    assert row.source == "MINIAPP_SYNC"
    assert row.received_at
    assert row.applied_at is None
    assert row.error_code is None


def test_record_envelope_distinct_events(session_factory: sessionmaker[Session]) -> None:
    """不同 event_id 各自落地，互不影响。"""
    repo = SyncRepository(session_factory)
    assert repo.record_envelope("evt-a", CIPHERTEXT_A, "MINIAPP_SYNC") is True
    assert repo.record_envelope("evt-b", CIPHERTEXT_B, "MINIAPP_SYNC") is True
    assert repo.get_envelope("evt-a") is not None
    assert repo.get_envelope("evt-b") is not None


def test_get_envelope_missing_returns_none(session_factory: sessionmaker[Session]) -> None:
    """查询不存在的 event_id 返回 None。"""
    repo = SyncRepository(session_factory)
    assert repo.get_envelope("no-such-event") is None


# ---------------------------------------------------------------------------
# mark_applied / mark_failed：应用状态回写
# ---------------------------------------------------------------------------


def test_mark_applied_clears_failure(session_factory: sessionmaker[Session]) -> None:
    """mark_applied：置 applied_at 并清空失败信息（重放修复后状态干净）。"""
    repo = SyncRepository(session_factory)
    repo.record_envelope("evt-2", CIPHERTEXT_A, "MINIAPP_SYNC")
    assert repo.mark_failed("evt-2", SYNC_ERROR_DECRYPT_FAILED, "密钥不符") is True

    row = repo.get_envelope("evt-2")
    assert row is not None
    assert row.error_code == SYNC_ERROR_DECRYPT_FAILED
    assert row.envelope_ciphertext == CIPHERTEXT_A  # 失败不丢密文

    assert repo.mark_applied("evt-2") is True
    row = repo.get_envelope("evt-2")
    assert row is not None
    assert row.applied_at is not None
    assert row.apply_error is None
    assert row.error_code is None


def test_mark_failed_records_error(session_factory: sessionmaker[Session]) -> None:
    """mark_failed：记 error_code / apply_error，密文保留，applied_at 不置位。"""
    repo = SyncRepository(session_factory)
    repo.record_envelope("evt-3", CIPHERTEXT_A, "MINIAPP_SYNC")

    assert repo.mark_failed("evt-3", SYNC_ERROR_APPLY_FAILED, "载荷类型不匹配") is True
    row = repo.get_envelope("evt-3")
    assert row is not None
    assert row.error_code == SYNC_ERROR_APPLY_FAILED
    assert row.apply_error == "载荷类型不匹配"
    assert row.applied_at is None
    assert row.envelope_ciphertext == CIPHERTEXT_A


def test_mark_on_missing_event_returns_false(session_factory: sessionmaker[Session]) -> None:
    """对不存在的 event_id 标记返回 False（不抛异常）。"""
    repo = SyncRepository(session_factory)
    assert repo.mark_applied("no-such-event") is False
    assert repo.mark_failed("no-such-event", SYNC_ERROR_APPLY_FAILED, "x") is False


# ---------------------------------------------------------------------------
# record_ack / mark_acked：ACK 流转
# ---------------------------------------------------------------------------


def test_record_ack_idempotent(session_factory: sessionmaker[Session]) -> None:
    """record_ack：首次 True，重复 ack_id False，状态初始 PENDING。"""
    repo = SyncRepository(session_factory)
    repo.record_envelope("evt-4", CIPHERTEXT_A, "MINIAPP_SYNC")

    assert repo.record_ack("env-4", "evt-4") is True
    assert repo.record_ack("env-4", "evt-4") is False  # 幂等：重复登记跳过

    pending = repo.list_pending_acks()
    assert [ack.ack_id for ack in pending] == ["env-4"]
    assert pending[0].event_id == "evt-4"
    assert pending[0].status == SYNC_ACK_STATUS_PENDING
    assert repo.pending_count() == 1


def test_mark_acked_flow_and_idempotent(session_factory: sessionmaker[Session]) -> None:
    """PENDING → ACKED 流转；重复 mark_acked 幂等返回 False，pending 归零。"""
    repo = SyncRepository(session_factory)
    repo.record_envelope("evt-5", CIPHERTEXT_A, "MINIAPP_SYNC")
    repo.record_ack("env-5", "evt-5")
    assert repo.pending_count() == 1

    assert repo.mark_acked("env-5") is True
    assert repo.mark_acked("env-5") is False  # 已 ACKED，幂等无害
    assert repo.pending_count() == 0
    assert repo.list_pending_acks() == []

    row = repo.get_envelope("evt-5")
    assert row is not None
    outbox = session_factory()
    try:
        ack = (
            outbox.query(SyncOutboxRow)
            .filter(SyncOutboxRow.ack_id == "env-5")
            .one()
        )
    finally:
        outbox.close()
    assert ack.status == SYNC_ACK_STATUS_ACKED  # 终态落地，非被删除


def test_pending_count_and_ordering(session_factory: sessionmaker[Session]) -> None:
    """pending_count 只统计 PENDING；list_pending_acks 按登记先后排序。"""
    repo = SyncRepository(session_factory)
    for i in range(3):
        repo.record_envelope(f"evt-{i}", CIPHERTEXT_A, "MINIAPP_SYNC")
        repo.record_ack(f"env-{i}", f"evt-{i}")
    assert repo.pending_count() == 3

    assert repo.mark_acked("env-1") is True
    assert repo.pending_count() == 2
    assert [ack.ack_id for ack in repo.list_pending_acks()] == ["env-0", "env-2"]

    assert repo.mark_acked("missing-ack") is False
