"""任务拉取与执行用例（M3 实装）。

规则：

1. 任务由云端定义、本地执行：完整结果留在本地，云端只收状态/耗时/版本/脱敏摘要；
2. ``list_tasks``：返回商户全部任务定义（含禁用的，UI 需明示启用状态）；
3. ``pull``：设备拉取待执行任务——CREATED 运行原子锁定为 QUEUED（已分发），
   锁定后不再被重复分发（断网重试由客户端按 run_id 幂等上报，M3 后续交付）；
4. 设备必须已注册且归属当前租户（租户隔离）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.application.errors import device_revoked, validation_failed
from app.application.ports import DeviceRepository, TaskRepository
from app.domain.task import Task, TaskRun

#: 设备单次拉取的默认上限
DEFAULT_PULL_LIMIT = 10


class TaskService:
    """调度任务应用服务。"""

    def __init__(self, tasks: TaskRepository, devices: DeviceRepository) -> None:
        self._tasks = tasks
        self._devices = devices

    def list_tasks(self, tenant_id: str) -> list[Task]:
        """列出商户全部任务定义（空租户返回空列表）。"""
        if not tenant_id:
            return []
        return self._tasks.list_tasks(tenant_id)

    def pull(
        self,
        *,
        tenant_id: str,
        device_id: str,
        limit: int = DEFAULT_PULL_LIMIT,
        now: datetime | None = None,
    ) -> list[tuple[Task, TaskRun]]:
        """设备拉取待执行任务：CREATED 运行锁定为 QUEUED，返回 (任务, 运行) 对。"""
        self._require_device(tenant_id, device_id)
        runs = self._tasks.pull_runs_for_device(
            tenant_id=tenant_id,
            device_id=device_id,
            limit=limit,
            now=now or datetime.now(UTC),
        )
        pairs: list[tuple[Task, TaskRun]] = []
        for run in runs:
            task = self._tasks.get_task(run.task_id)
            if task is not None:
                pairs.append((task, run))
        return pairs

    def _require_device(self, tenant_id: str, device_id: str) -> None:
        """校验设备已注册、归属本租户且未吊销。"""
        device = self._devices.get_device(device_id)
        if device is None or device.tenant_id != tenant_id:
            raise validation_failed(
                "设备不存在或不属于当前商户。",
                reason="DEVICE_NOT_FOUND",
                device_id=device_id,
            )
        if device.is_revoked():
            raise device_revoked(device_id=device_id)
