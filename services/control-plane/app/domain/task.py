"""调度任务与运行投影（M3 实装）。

- ``Task``：云端定义的调度任务（cron 表达式 + 范围），任务由云端定义、本地执行；
- ``TaskRun``：一次运行的投影。完整结果留在本地，云端只收状态/耗时/版本/脱敏摘要
  （主基线 §35.6「任务、运行和运维组」）；
- 状态机取值对齐 0001_control_meta 播种的 ``run_status`` 枚举（主基线 §32.1）：
  ``CREATED -> QUEUED -> RUNNING -> SUCCEEDED/FAILED``，
  ``CANCELLED`` / ``MISSED`` 为终态，``RETRYING`` 等待重新分发。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class RunStatus(str, Enum):
    """任务运行状态（与 0001_control_meta 的 run_status 种子一致）。"""

    CREATED = "CREATED"  # 云端已创建，待分发
    QUEUED = "QUEUED"  # 已分发给设备（拉取时锁定）
    RUNNING = "RUNNING"  # 设备执行中
    SUCCEEDED = "SUCCEEDED"  # 成功（终态）
    FAILED = "FAILED"  # 失败（可转重试）
    CANCELLED = "CANCELLED"  # 已取消（终态）
    MISSED = "MISSED"  # 错过调度窗口（终态）
    RETRYING = "RETRYING"  # 等待重新分发


#: 允许的状态迁移表（源状态 -> 可达状态集合）
RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.QUEUED, RunStatus.CANCELLED}),
    RunStatus.QUEUED: frozenset(
        {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.MISSED}
    ),
    RunStatus.RUNNING: frozenset(
        {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.FAILED: frozenset({RunStatus.RETRYING, RunStatus.CANCELLED}),
    RunStatus.RETRYING: frozenset({RunStatus.QUEUED, RunStatus.CANCELLED}),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.MISSED: frozenset(),
}


@dataclass
class Task:
    """调度任务实体：任务类型、cron 表达式与执行范围。"""

    task_id: str
    tenant_id: str
    task_type: str
    cron_expr: str | None = None
    scope: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    created_by: str | None = None
    created_at: datetime | None = None


@dataclass
class TaskRun:
    """任务运行投影：只保存状态与摘要，不保存业务明细原文。"""

    run_id: str
    task_id: str
    tenant_id: str
    status: RunStatus = RunStatus.CREATED
    device_id: str | None = None
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def transition(self, target: RunStatus) -> None:
        """按状态机迁移；非法迁移抛 ValueError。"""
        if target not in RUN_TRANSITIONS[self.status]:
            raise ValueError(f"非法状态迁移：{self.status.value} -> {target.value}")
        self.status = target
