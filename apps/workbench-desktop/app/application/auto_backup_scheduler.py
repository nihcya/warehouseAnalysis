"""自动备份调度器（M3 Task 5，SubTask 5.2）：QTimer 主线程驱动。

- 每 60 秒检查一次时间（``QTimer``，挂在主线程事件循环上，不引入后台
  线程——备份操作本身是短事务 VACUUM INTO，不必另起线程）；
- 本地时间每天首次到达 ``backup_at``（缺省 03:00）时触发一次自动备份
  （AUTO 类型，经组合根注入的 ``BackupManager.run_manual_backup`` 同链路）；
- 当日是否已备份由 ``last_auto_backup.json`` 持久化记录（``BackupService``
  本身不维护"最近备份时间"持久化），当日已备份则跳过，避免重启重复备份；
- 状态文件同时记录上次备份时间戳 ``last_backup_at``：距上次备份已超过
  24 小时（如连续多日未开机）则启动/周期检查时立即补一次，不必等到
  ``backup_hour``（任务书要求"启动时检查上次备份时间，过 24h 立即补一次"）；
- 备份结果经 :attr:`backup_finished` 信号通知 UI（托盘提示），不弹模态框。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

#: 状态文件名（%LOCALAPPDATA%/WarehouseWorkbench/last_auto_backup.json）
LAST_BACKUP_FILENAME = "last_auto_backup.json"

#: 检查间隔（毫秒）：每分钟看一眼"今天到点没备份吗"
CHECK_INTERVAL_MS = 60_000

#: 每日自动备份时刻（本地时间，小时）
DEFAULT_BACKUP_HOUR = 3

#: 距上次自动备份超过该时长即视为"漏备"：启动/周期检查时立即补一次，
#: 不必等到当日 ``backup_hour``（时间戳取自状态文件 ``last_backup_at``）
CATCH_UP_AFTER = timedelta(hours=24)

#: 默认数据目录名（与 local_data.connection.APP_DIR_NAME、TokenStore 同根，
#: 此处复制字面量以保持 application 层不 import infrastructure/local_data）
APP_DATA_DIR_NAME = "WarehouseWorkbench"


def default_state_path() -> Path:
    """状态文件默认路径：%LOCALAPPDATA%/WarehouseWorkbench/last_auto_backup.json。"""
    import os

    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home()
    return root / APP_DATA_DIR_NAME / LAST_BACKUP_FILENAME


class AutoBackupScheduler(QObject):
    """每日一次自动备份调度（注入备份回调与时钟，便于测试）。

    :param run_backup: 备份执行回调（组合根注入 ``BackupManager.run_manual_backup``），
        返回 ``BackupOperationStatus``（``ok`` 表示成功）；异常由本调度器吞掉并
        发出失败信号，绝不影响主流程。
    :param state_path: ``last_auto_backup.json`` 状态文件路径（测试传 tmp 隔离）。
    :param clock: 当前本地时间提供方（测试注入 fake clock）。
    :param backup_hour: 每日备份时刻（本地时间小时，缺省 3 点）。
    :param parent: Qt 父对象。
    """

    #: 备份完成信号：ok=是否成功，message=中文结果描述
    backup_finished = Signal(bool, str)

    def __init__(
        self,
        run_backup: Callable[[], object],
        state_path: Path | None = None,
        clock: Callable[[], datetime] | None = None,
        backup_hour: int = DEFAULT_BACKUP_HOUR,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._run_backup = run_backup
        self._state_path = (
            state_path if state_path is not None else default_state_path()
        )
        self._clock = clock if clock is not None else datetime.now
        self._backup_hour = backup_hour
        self._timer = QTimer(self)
        self._timer.setInterval(CHECK_INTERVAL_MS)
        self._timer.timeout.connect(self.check_and_backup)

    def start(self) -> None:
        """启动调度：先立即检查一次（覆盖跨天与超 24h 漏备场景），再进入周期检查。"""
        self.check_and_backup()
        self._timer.start()

    def stop(self) -> None:
        """停止调度（应用退出时调用）。"""
        self._timer.stop()

    # ------------------------------------------------------------------
    # 状态文件读写（last_auto_backup.json：日期 + 时间戳）
    # ------------------------------------------------------------------

    def _read_state(self) -> dict[str, object]:
        """读状态文件原始 JSON；缺失/损坏/非对象返回空 dict。"""
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _read_last_backup_date(self) -> str | None:
        """读最后备份日期（ISO yyyy-mm-dd）；缺失/损坏返回 None。"""
        date = self._read_state().get("last_backup_date")
        return date if isinstance(date, str) else None

    def _read_last_backup_at(self) -> datetime | None:
        """读最后备份时间戳（``last_backup_at``，ISO 8601）；缺失/损坏返回 None。"""
        raw = self._read_state().get("last_backup_at")
        if not isinstance(raw, str):
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def _record_backup_started(self, now: datetime) -> None:
        """写状态文件：当日日期 + 本次触发时间戳（失败仅吞掉：丢失代价只是多备一次）。"""
        payload = {
            "last_backup_date": now.strftime("%Y-%m-%d"),
            "last_backup_at": now.isoformat(),
        }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _is_already_backed_up_today(self, now: datetime) -> bool:
        """当日已备份判定：状态文件日期 == 今天。"""
        return self._read_last_backup_date() == now.strftime("%Y-%m-%d")

    def _is_overdue(self, now: datetime) -> bool:
        """漏备判定：已知上次备份时间且距 ``now`` 已超过 :data:`CATCH_UP_AFTER`。

        时间戳缺失（旧版本状态文件/从未备份）不算漏备，退回每日到点逻辑；
        朴素/感知时间混用无法相减时同样退回每日逻辑，绝不因此抛异常。
        """
        last_at = self._read_last_backup_at()
        if last_at is None:
            return False
        try:
            return now - last_at >= CATCH_UP_AFTER
        except TypeError:
            return False

    # ------------------------------------------------------------------
    # 到点检查与触发
    # ------------------------------------------------------------------

    def check_and_backup(self) -> bool:
        """单次检查：到点且当日未备份、或漏备超 24 小时，则触发备份。

        - 距上次备份已超 24 小时（``last_backup_at`` 判定）→ 立即补一次，
          不必等到 ``backup_hour``（覆盖"连续多日未开机"场景）；
        - 未到 ``backup_hour`` 点且无漏备 → 跳过；
        - 当日已备份（状态文件日期）且无漏备 → 跳过；
        - 触发后无论成功失败都写状态（失败不重试，避免每分钟反复失败打扰）。
        """
        now = self._clock()
        if not self._is_overdue(now):
            if now.hour < self._backup_hour:
                return False
            if self._is_already_backed_up_today(now):
                return False
        self._record_backup_started(now)
        self._execute_backup()
        return True

    def _execute_backup(self) -> None:
        """执行备份并把结果翻译成信号（异常不外抛，调度器绝不拖垮主流程）。"""
        try:
            status = self._run_backup()
            ok = bool(getattr(status, "ok", False))
            message = str(getattr(status, "message", ""))
        except Exception as exc:  # noqa: BLE001 —— 备份失败不阻断主流程
            ok, message = False, f"自动备份失败：{exc}"
        self.backup_finished.emit(ok, message)
