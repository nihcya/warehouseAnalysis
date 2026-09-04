"""设备心跳最新投影（M3 实装）。

``device_id`` 主键、一行一台设备：每次心跳 upsert 覆盖为最新投影
（主基线 §35.6「任务、运行和运维组」）。
``status`` 为工作台 Agent 自报的运行状态文本（如 ``RUNNING`` / ``IDLE``），
云端仅作投影展示，不做状态机约束。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Heartbeat:
    """设备心跳投影：版本三元组 + 待同步数 + 运行状态。"""

    device_id: str
    tenant_id: str
    sent_at: datetime
    status: str
    app_version: str | None = None
    engine_version: str | None = None
    db_schema_version: str | None = None
    pending_sync_count: int = 0
