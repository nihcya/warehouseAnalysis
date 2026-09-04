"""小程序事件同步后台线程（M3 Task 4）：拉取加密信封 → 解密 → 落库 → ACK。

处理链路（每轮）::

    重发历史 PENDING ACK → pull_sync_events
      → record_envelope（event_id 幂等，重复下发直接跳过）
      → 解密（失败：DECRYPT_FAILED，密文保留待换钥重放）
      → 校验 payload（类型不匹配：APPLY_FAILED）
      → 事务落库 inventory_event（幂等 upsert，重复 event_id 静默跳过）
      → mark_applied → record_ack（PENDING）→ ack_sync_events → ACKED

一致性约定：**落库成功为准**——ACK 失败不回滚落库，ACK 留在 sync_outbox
为 PENDING，下轮循环重发（云端 ACK 幂等）；落库失败同样不 ACK，下轮
拉取时云端重新下发该信封，event_id 幂等保证不重复应用。

断网指数退避：连续失败翻倍（BASE → CAP 上限 600s），恢复立即拉取；
所有等待均可被 stop() 中断。
"""

from __future__ import annotations

import json
import os
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from local_data.models import SYNC_ERROR_APPLY_FAILED, SYNC_ERROR_DECRYPT_FAILED
from local_data.repository import InventoryEventCreate, InventoryEventRepository
from local_data.sync_repository import SyncRepository
from PySide6.QtCore import QThread, Signal
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

#: 解密信封所需的 Fernet 密钥环境变量（与云端 SYNC_ENCRYPTION_KEY 一致）
SYNC_ENCRYPTION_KEY_ENV = "SYNC_ENCRYPTION_KEY"

#: 默认单轮拉取的信封数量上限（云端契约 1-200）
DEFAULT_PULL_LIMIT = 50

#: 同步错误码（与 local_data.models 同步组一致）
ERROR_DECRYPT_FAILED = SYNC_ERROR_DECRYPT_FAILED
ERROR_APPLY_FAILED = SYNC_ERROR_APPLY_FAILED

#: 事件源标识（落库 inventory_event.source）
SOURCE_MINIAPP_SYNC = "MINIAPP_SYNC"


class SyncWorker(QThread):
    """后台同步线程：循环「重发 PENDING ACK → 拉信封 → 解密 → 落库 → ACK」。

    用法::

        worker = SyncWorker(
            api_client=http_client,
            session_factory=session_factory,
            device_id="dev-001",
        )
        worker.sync_progress.connect(update_status_bar)
        worker.start()
        # ...
        worker.stop()
        worker.wait()
    """

    #: 每轮同步结果：(applied, failed, pending)
    sync_progress = Signal(int, int, int)
    #: 错误消息（解密/应用失败、网络异常等）
    error_occurred = Signal(str)

    #: 空轮询间隔（云端无新信封且无待重试 ACK 时，秒）
    _IDLE_INTERVAL = 5.0
    #: 断网退避基数（秒）
    _BACKOFF_BASE = 2.0
    #: 断网退避上限（秒）
    _BACKOFF_CAP = 600.0
    #: 可中断睡眠的步长（秒）
    _SLEEP_STEP = 0.05

    def __init__(
        self,
        api_client: Any,
        session_factory: sessionmaker[Session],
        device_id: str,
        *,
        limit: int = DEFAULT_PULL_LIMIT,
        key: str | None = None,
    ) -> None:
        """初始化同步线程。

        :param api_client: HttpApiClient（或兼容 duck-typing 的客户端，
            测试可注入 fake），需提供 ``pull_sync_events`` / ``ack_sync_events``。
        :param session_factory: 本地库 sessionmaker（Alembic 迁移后构建）。
        :param device_id: 本设备标识（云端按此定向下发信封）。
        :param limit: 单轮拉取信封上限（云端契约 1-200）。
        :param key: Fernet 密钥（urlsafe base64 32 字节）；缺省从
            ``SYNC_ENCRYPTION_KEY`` 环境变量读取。
        """
        super().__init__()
        self._api_client = api_client
        self._sync_repo = SyncRepository(session_factory)
        self._event_repo = InventoryEventRepository(session_factory)
        self._device_id = device_id
        self._limit = limit
        self._key = key
        self._stop = False

    def stop(self) -> None:
        """请求线程退出（设置停止标志，run 循环将在下次检查时退出）。"""
        self._stop = True

    def set_device_id(self, device_id: str) -> None:
        """设置设备标识（组合根在设备注册成功后、start() 前调用）。"""
        self._device_id = device_id

    def pending_count(self) -> int:
        """当前待同步事件数（云端尚未确认 ACK 的数量），供心跳载荷与 UI 读取。"""
        return self._sync_repo.pending_count()

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def run(self) -> None:
        """线程主循环：每轮同步，空轮低速空转、失败指数退避，直到 stop()。"""
        backoff = self._BACKOFF_BASE
        while not self._stop:
            applied, failed, pending, ok = self._run_cycle()
            self.sync_progress.emit(applied, failed, pending)
            if not ok:
                # 网络失败：指数退避（上限 600s），恢复后立即拉取
                self._sleep(backoff)
                backoff = min(backoff * 2, self._BACKOFF_CAP)
            elif applied == 0 and failed == 0:
                # 空轮（云端无新信封、无待重试 ACK）：低速空转
                self._sleep(self._IDLE_INTERVAL)

    def _run_cycle(self) -> tuple[int, int, int, bool]:
        """执行一轮同步，返回 ``(applied, failed, pending, ok)``。

        - ok=False：网络失败（ACK 重发或 pull 请求层失败），触发指数退避；
        - pending：云端尚未确认的 ACK 数量（含本轮新登记与历史遗留）。
        """
        applied = 0
        failed = 0
        ok = True

        # 0. 先重发历史 PENDING 的 ACK（上一轮网络失败的遗留；云端幂等）
        for pending_ack in self._sync_repo.list_pending_acks():
            if self._stop:
                break
            sent = self._api_client.ack_sync_events(pending_ack.ack_id)
            if sent is None:
                ok = False
                break
            self._sync_repo.mark_acked(pending_ack.ack_id)

        # 1. 拉取新信封；网络失败直接结束本轮（ok=False）
        envelopes = self._api_client.pull_sync_events(self._device_id, self._limit)
        if envelopes is None:
            return applied, failed, self._sync_repo.pending_count(), False

        # 2. 逐个处理信封（落库 + ACK）
        for envelope in envelopes:
            if self._stop:
                break
            cycle_applied, cycle_failed = self._process_envelope(envelope)
            applied += cycle_applied
            failed += cycle_failed

        return applied, failed, self._sync_repo.pending_count(), ok

    # ------------------------------------------------------------------
    # 单信封处理：落库 → ACK
    # ------------------------------------------------------------------

    def _process_envelope(self, envelope: dict[str, Any]) -> tuple[int, int]:
        """处理单个信封，返回 ``(applied, failed)`` 计数增量。"""
        event_id = str(envelope.get("event_id", ""))
        ciphertext = str(envelope.get("ciphertext", ""))
        envelope_id = str(envelope.get("envelope_id", ""))

        # 1. 幂等落地信封（已存在的 event_id 直接跳过，不重复解密/落库/ACK）
        if not self._sync_repo.record_envelope(
            event_id, ciphertext, SOURCE_MINIAPP_SYNC
        ):
            return 0, 0

        # 2. 解密（失败保留密文，换钥或修复后可重放；不下发 ACK）
        try:
            payload = self._decrypt(ciphertext)
        except InvalidToken:
            self._sync_repo.mark_failed(
                event_id, ERROR_DECRYPT_FAILED, "信封解密失败（密钥不符或密文损坏）"
            )
            self.error_occurred.emit(f"信封解密失败：{event_id}")
            return 0, 1

        # 3. 校验 payload 并构造事件；类型不匹配 → APPLY_FAILED（不下发 ACK）
        event = self._build_event(event_id, payload)
        if event is None:
            self._sync_repo.mark_failed(
                event_id, ERROR_APPLY_FAILED, "事件载荷类型不匹配，无法落库"
            )
            self.error_occurred.emit(f"事件载荷类型不匹配：{event_id}")
            return 0, 1

        # 4. 幂等落库（重复 event_id 静默跳过）；约束冲突 → APPLY_FAILED
        try:
            self._event_repo.upsert_events([event])
        except IntegrityError:
            self._sync_repo.mark_failed(
                event_id, ERROR_APPLY_FAILED, "落库失败：主数据缺失或约束冲突"
            )
            self.error_occurred.emit(f"事件落库失败（约束冲突）：{event_id}")
            return 0, 1

        # 5. 标记已应用并登记 ACK；ACK 失败不回滚落库，PENDING 留待下轮重发
        self._sync_repo.mark_applied(event_id)
        if self._sync_repo.record_ack(envelope_id, event_id):
            sent = self._api_client.ack_sync_events(envelope_id)
            if sent is not None:
                self._sync_repo.mark_acked(envelope_id)
        return 1, 0

    # ------------------------------------------------------------------
    # 解密与载荷校验
    # ------------------------------------------------------------------

    def _decrypt(self, ciphertext: str) -> dict[str, Any]:
        """解密信封（与云端 ``sync_crypto.encrypt_json`` 对称的本地侧实现）。

        密钥不符或密文损坏抛 ``InvalidToken``。
        """
        key = self._key if self._key is not None else os.environ.get(SYNC_ENCRYPTION_KEY_ENV, "")
        fernet = Fernet(key.encode("utf-8"))
        plain = fernet.decrypt(ciphertext.encode("utf-8"))
        return dict(json.loads(plain.decode("utf-8")))

    def _build_event(
        self, event_id: str, payload: dict[str, Any]
    ) -> InventoryEventCreate | None:
        """把解密后的 payload 校验为 InventoryEventCreate；类型不匹配返回 None。

        必填字段：event_id / sku_id / warehouse_id / move_type / quantity /
        occurred_at / source；quantity 为十进制字符串（禁止 float 真值）；
        payload.event_id 必须与信封 event_id 一致（完整性校验）。
        """
        try:
            if str(payload["event_id"]) != event_id:
                return None
            quantity = Decimal(str(payload["quantity"]))
            return InventoryEventCreate(
                event_id=str(payload["event_id"]),
                sku_id=str(payload["sku_id"]),
                warehouse_id=str(payload["warehouse_id"]),
                move_type=str(payload["move_type"]),
                quantity=quantity,
                occurred_at=str(payload["occurred_at"]),
                source=str(payload["source"]),
            )
        except (KeyError, InvalidOperation, ValueError, TypeError):
            return None

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _sleep(self, seconds: float) -> None:
        """可被 stop 中断的等待（按步长轮询 _stop 标志）。"""
        remaining = seconds
        while remaining > 0 and not self._stop:
            step = min(self._SLEEP_STEP, remaining)
            time.sleep(step)
            remaining -= step
