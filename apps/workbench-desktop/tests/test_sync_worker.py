"""SyncWorker 同步链路测试（M3 Task 4）：fake http client + 临时 SQLite。

覆盖：
- 成功落库 + ACK（inventory_event 落库、信封 applied、outbox ACKED、pending 归零）；
- 解密失败保留密文（DECRYPT_FAILED，不落库、不 ACK，云端未收到确认）；
- payload 类型不匹配（APPLY_FAILED，不 ACK）；
- event_id 重复幂等（云端重复下发只落库/ACK 一次）；
- ACK 失败下轮重试（PENDING 留在 outbox，网络恢复后补发成功）。

等待策略：worker 有处理进度的轮次（applied/failed > 0）后不休眠、立即进入
下一轮，因此 qtbot.waitUntil 轮询 progress 列表即可覆盖全部断言时机；
仅空轮才睡 IDLE_INTERVAL，不影响判定。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from cryptography.fernet import Fernet
from local_data.repository import InventoryEventRepository, MasterDataRepository
from local_data.sync_repository import SyncRepository
from sqlalchemy.orm import Session, sessionmaker
from workbench.workers.sync_worker import (
    ERROR_APPLY_FAILED,
    ERROR_DECRYPT_FAILED,
    SOURCE_MINIAPP_SYNC,
    SyncWorker,
)

#: 等待信号的超时上限（毫秒）：覆盖线程启动 + 2-3 轮循环
WAIT_TIMEOUT_MS = 10000

#: 测试专用 Fernet 密钥（urlsafe base64 32 字节，与云端生成口径一致）
KEY = Fernet.generate_key().decode("utf-8")


def encrypt_payload(payload: dict[str, Any]) -> str:
    """与云端 sync_crypto.encrypt_json 相同口径的加密（测试镜像实现）。"""
    fernet = Fernet(KEY.encode("utf-8"))
    plain = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return fernet.encrypt(plain).decode("utf-8")


def make_event_payload(event_id: str, quantity: str = "5") -> dict[str, Any]:
    """构造合法的小程序事件载荷（云端下发前的明文）。"""
    return {
        "event_id": event_id,
        "sku_id": "SKU-0001",
        "warehouse_id": "WH-01",
        "move_type": "INBOUND",
        "quantity": quantity,
        "occurred_at": "2026-09-01T08:00:00+00:00",
        "source": "MINIAPP",
    }


def make_envelope(event_id: str, ciphertext: str) -> dict[str, Any]:
    """构造云端 SyncEnvelopeData 形状的信封字典。"""
    return {
        "envelope_id": f"env-{event_id}",
        "event_id": event_id,
        "target_device_id": "dev-test",
        "ciphertext": ciphertext,
        "idempotency_key": None,
        "status": "ENQUEUED",
        "expires_at": None,
        "created_at": "2026-09-01T08:00:00+00:00",
    }


class FakeSyncApiClient:
    """同步客户端测试桩：pull 按页出队（耗尽后返回空表），ack 可注入失败。"""

    def __init__(self, pull_pages: list[list[dict[str, Any]]] | None = None) -> None:
        self._pull_pages = list(pull_pages or [])
        self.pull_calls = 0
        self.pulled_device_ids: list[str] = []
        self.acked_envelope_ids: list[str] = []
        self.ack_fail_remaining: dict[str, int] = {}
        self.ack_fail_all = False

    def pull_sync_events(
        self, device_id: str, limit: int = 50
    ) -> list[dict[str, Any]] | None:
        """按预设页序返回信封，耗尽后返回空列表（空轮）。"""
        self.pull_calls += 1
        self.pulled_device_ids.append(device_id)
        if self._pull_pages:
            return self._pull_pages.pop(0)
        return []

    def ack_sync_events(self, envelope_id: str) -> dict[str, Any] | None:
        """确认信封：注入失败次数未耗尽时返回 None（模拟网络失败）。"""
        if self.ack_fail_all:
            return None
        remaining = self.ack_fail_remaining.get(envelope_id, 0)
        if remaining > 0:
            self.ack_fail_remaining[envelope_id] = remaining - 1
            return None
        self.acked_envelope_ids.append(envelope_id)
        return {"envelope_id": envelope_id, "already_acked": False}


@pytest.fixture
def seeded_master(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    """主数据前置（WH-01 + SKU-0001），同步落库的 FK 依赖。"""
    master = MasterDataRepository(session_factory)
    master.add_warehouse(warehouse_id="WH-01", name="主仓")
    master.add_sku(sku_id="SKU-0001", name="矿泉水 550ml")
    return session_factory


@pytest.fixture
def make_worker(qtbot: Any, seeded_master: sessionmaker[Session]) -> Any:
    """worker 工厂：统一 stop + wait 收尾，避免线程泄漏到后续用例。

    依赖 qtbot 以确保 QApplication 先于任何 QThread 构造。
    """
    workers: list[SyncWorker] = []

    def _make(client: FakeSyncApiClient) -> SyncWorker:
        worker = SyncWorker(
            api_client=client,
            session_factory=seeded_master,
            device_id="dev-test",
            key=KEY,
        )
        workers.append(worker)
        return worker

    yield _make
    for worker in workers:
        worker.stop()
    for worker in workers:
        worker.wait(5000)


def _wait_for_progress(
    qtbot: Any,
    progress: list[tuple[int, int, int]],
    predicate: Callable[[int, int, int], bool],
) -> None:
    """在 Qt 事件循环内轮询，直到某轮 sync_progress 快照满足谓词。"""
    qtbot.waitUntil(
        lambda: any(predicate(a, f, p) for a, f, p in progress),
        timeout=WAIT_TIMEOUT_MS,
    )


# ---------------------------------------------------------------------------
# 成功链路
# ---------------------------------------------------------------------------


def test_apply_success_and_ack(
    qtbot: Any, make_worker: Any, seeded_master: sessionmaker[Session]
) -> None:
    """成功链路：解密 → 落库 → applied → ACK；pending 归零，事件可查。"""
    envelope = make_envelope("EVT-1", encrypt_payload(make_event_payload("EVT-1")))
    client = FakeSyncApiClient(pull_pages=[[envelope]])
    worker = make_worker(client)
    progress: list[tuple[int, int, int]] = []
    worker.sync_progress.connect(lambda a, f, p: progress.append((a, f, p)))
    worker.start()

    # 等待第一轮完成（applied=1, failed=0, pending=0）
    _wait_for_progress(qtbot, progress, lambda a, f, p: (a, f, p) == (1, 0, 0))

    events = InventoryEventRepository(seeded_master)
    event = events.get_event("EVT-1")
    assert event is not None
    assert event.quantity == Decimal(5)
    assert event.source == "MINIAPP"

    sync_repo = SyncRepository(seeded_master)
    row = sync_repo.get_envelope("EVT-1")
    assert row is not None
    assert row.source == SOURCE_MINIAPP_SYNC
    assert row.applied_at is not None
    assert row.error_code is None

    assert client.acked_envelope_ids == ["env-EVT-1"]
    assert sync_repo.pending_count() == 0


# ---------------------------------------------------------------------------
# 解密失败：保留密文、不 ACK
# ---------------------------------------------------------------------------


def test_decrypt_failure_keeps_ciphertext(
    qtbot: Any, make_worker: Any, seeded_master: sessionmaker[Session]
) -> None:
    """解密失败：DECRYPT_FAILED、密文保留、不落库、云端未收到 ACK。"""
    envelope = make_envelope("EVT-BAD", "not-a-valid-fernet-token")
    client = FakeSyncApiClient(pull_pages=[[envelope]])
    worker = make_worker(client)
    progress: list[tuple[int, int, int]] = []
    errors: list[str] = []
    worker.sync_progress.connect(lambda a, f, p: progress.append((a, f, p)))
    worker.error_occurred.connect(errors.append)
    worker.start()

    _wait_for_progress(qtbot, progress, lambda a, f, p: f >= 1)

    assert progress[0] == (0, 1, 0)
    assert errors, "解密失败应发 error_occurred"

    sync_repo = SyncRepository(seeded_master)
    row = sync_repo.get_envelope("EVT-BAD")
    assert row is not None
    assert row.envelope_ciphertext == "not-a-valid-fernet-token"  # 密文原样保留
    assert row.error_code == ERROR_DECRYPT_FAILED
    assert row.applied_at is None

    assert InventoryEventRepository(seeded_master).get_event("EVT-BAD") is None
    assert client.acked_envelope_ids == []
    assert sync_repo.pending_count() == 0


# ---------------------------------------------------------------------------
# 载荷类型不匹配：APPLY_FAILED、不 ACK
# ---------------------------------------------------------------------------


def test_payload_mismatch_marks_apply_failed(
    qtbot: Any, make_worker: Any, seeded_master: sessionmaker[Session]
) -> None:
    """payload 缺必填字段：APPLY_FAILED、不落库、不下发 ACK。"""
    envelope = make_envelope("EVT-TYPE", encrypt_payload({"event_id": "EVT-TYPE"}))
    client = FakeSyncApiClient(pull_pages=[[envelope]])
    worker = make_worker(client)
    progress: list[tuple[int, int, int]] = []
    worker.sync_progress.connect(lambda a, f, p: progress.append((a, f, p)))
    worker.start()

    _wait_for_progress(qtbot, progress, lambda a, f, p: f >= 1)

    assert progress[0] == (0, 1, 0)
    sync_repo = SyncRepository(seeded_master)
    row = sync_repo.get_envelope("EVT-TYPE")
    assert row is not None
    assert row.error_code == ERROR_APPLY_FAILED
    assert row.applied_at is None
    assert client.acked_envelope_ids == []
    assert InventoryEventRepository(seeded_master).get_event("EVT-TYPE") is None


# ---------------------------------------------------------------------------
# event_id 重复幂等
# ---------------------------------------------------------------------------


def test_duplicate_event_id_applied_once(
    qtbot: Any, make_worker: Any, seeded_master: sessionmaker[Session]
) -> None:
    """云端重复下发同一信封：第二轮回落 record_envelope=False，不重复 ACK。"""
    envelope = make_envelope("EVT-DUP", encrypt_payload(make_event_payload("EVT-DUP")))
    client = FakeSyncApiClient(pull_pages=[[envelope], [dict(envelope)]])
    worker = make_worker(client)
    progress: list[tuple[int, int, int]] = []
    worker.sync_progress.connect(lambda a, f, p: progress.append((a, f, p)))
    worker.start()

    # 等到第二轮拉取完成（重复信封被跳过：applied=0, failed=0，pending=0）
    _wait_for_progress(
        qtbot, progress, lambda a, f, p: client.pull_calls >= 2 and p == 0
    )

    assert progress[0] == (1, 0, 0)  # 第一轮正常应用
    assert client.acked_envelope_ids == ["env-EVT-DUP"]  # 只 ACK 一次
    assert InventoryEventRepository(seeded_master).get_event("EVT-DUP") is not None
    assert SyncRepository(seeded_master).get_envelope("EVT-DUP") is not None


# ---------------------------------------------------------------------------
# ACK 失败：下轮重试
# ---------------------------------------------------------------------------


def test_ack_failure_retried_next_cycle(
    qtbot: Any, make_worker: Any, seeded_master: sessionmaker[Session]
) -> None:
    """首次 ACK 失败：落库成功为准（applied），PENDING 留 outbox，下轮补发成功。"""
    envelope = make_envelope(
        "EVT-RETRY", encrypt_payload(make_event_payload("EVT-RETRY"))
    )
    client = FakeSyncApiClient(pull_pages=[[envelope]])
    client.ack_fail_remaining["env-EVT-RETRY"] = 1  # 第一次 ACK 失败，之后恢复
    worker = make_worker(client)
    progress: list[tuple[int, int, int]] = []
    worker.sync_progress.connect(lambda a, f, p: progress.append((a, f, p)))
    worker.start()

    _wait_for_progress(
        qtbot, progress, lambda a, f, p: bool(p == 0 and client.acked_envelope_ids)
    )

    assert progress[0] == (1, 0, 1)  # 第一轮：落库成功，ACK 未确认（pending=1）
    assert client.acked_envelope_ids == ["env-EVT-RETRY"]  # 第二轮补发，恰好一次成功

    sync_repo = SyncRepository(seeded_master)
    row = sync_repo.get_envelope("EVT-RETRY")
    assert row is not None
    assert row.applied_at is not None  # 落库成功为准，未被 ACK 失败回滚
    assert sync_repo.pending_count() == 0


# ---------------------------------------------------------------------------
# 总览页待同步数接线（M3 Task 5）：outbox PENDING → sync_progress(pending)
# ---------------------------------------------------------------------------


def test_pending_outbox_drives_sync_progress_without_pull(
    qtbot: Any, make_worker: Any, seeded_master: sessionmaker[Session]
) -> None:
    """outbox 中已有 PENDING ACK：首轮零拉取也会经 sync_progress 上报 pending=2。

    这是总览页"待同步：N"的数据链路（pending=等待云端确认的 ACK 数）。
    ACK 端点持续失败：两轮重发均未确认，pending 保持 2 并随轮次发射。
    """
    sync_repo = SyncRepository(seeded_master)
    sync_repo.record_envelope("EVT-A", "cipher-a", SOURCE_MINIAPP_SYNC)
    sync_repo.record_envelope("EVT-B", "cipher-b", SOURCE_MINIAPP_SYNC)
    sync_repo.record_ack("ack-1", "EVT-A")
    sync_repo.record_ack("ack-2", "EVT-B")
    assert sync_repo.pending_count() == 2

    client = FakeSyncApiClient()  # 拉取永远为空，pending 只能来自 outbox
    client.ack_fail_all = True  # ACK 全部失败，pending 维持 2
    worker = make_worker(client)
    progress: list[tuple[int, int, int]] = []
    worker.sync_progress.connect(lambda a, f, p: progress.append((a, f, p)))
    worker.start()

    _wait_for_progress(qtbot, progress, lambda a, f, p: p == 2)

    assert client.pull_calls >= 1
    assert progress[0] == (0, 0, 2)
    assert client.acked_envelope_ids == []
