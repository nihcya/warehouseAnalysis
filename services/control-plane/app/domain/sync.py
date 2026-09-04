"""小程序事件同步信封（M3 实装）。

- 事件以**密文信封**中继：云端只存密文（Fernet 对称加密），明文不出现在控制库
  （主基线 §35.5 / SECURITY.md）；
- ``event_id`` 全局唯一：客户端幂等落库由唯一约束兜底，重复 ACK 幂等成功；
- ``expires_at`` 为 TTL：拉取时顺带清理过期信封，客户端断网重试期间信封保持
  ENQUEUED（待拉取）状态。

状态取值对齐 0001_control_meta 播种的 ``sync_status`` 枚举（主基线 §32.2）；
spec 语义上的「PENDING（待拉取）」对应 ``ENQUEUED``。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SyncEnvelopeStatus(str, Enum):
    """同步信封状态（与 0001_control_meta 的 sync_status 种子一致）。"""

    CREATED = "CREATED"  # 已创建（预留）
    ENQUEUED = "ENQUEUED"  # 已入队待拉取（spec 语义上的 PENDING）
    DELIVERED = "DELIVERED"  # 已投递（预留）
    APPLIED = "APPLIED"  # 已应用（预留）
    ACKED = "ACKED"  # 已确认（终态）
    EXPIRED = "EXPIRED"  # 已过期（TTL 清理）
    REJECTED = "REJECTED"  # 已拒绝（校验失败且不可重试）
    RETRYING = "RETRYING"  # 客户端请求重试


@dataclass
class SyncEnvelope:
    """加密信封：密文 + 幂等键 + TTL。"""

    envelope_id: str
    tenant_id: str
    target_device_id: str
    event_id: str
    ciphertext: str
    idempotency_key: str | None = None
    status: SyncEnvelopeStatus = SyncEnvelopeStatus.ENQUEUED
    expires_at: datetime | None = None
    created_at: datetime | None = None
    acked_at: datetime | None = None
