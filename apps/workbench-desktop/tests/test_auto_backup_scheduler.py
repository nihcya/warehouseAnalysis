"""自动备份调度器测试（M3 Task 5，SubTask 5.2）。

覆盖（fake backup service + fake clock，纯逻辑，不启动事件循环）：
- 当日首次到点触发：执行一次备份，状态文件写入当日日期与时间戳；
- 当日重复触发（周期检查/重启后）：状态文件日期为今天则跳过，不重复备份；
- 未到 ``backup_hour`` 点：即使当日未备份也跳过，且不写状态文件；
- 跨天：昨日状态不拦截今日备份（``start()`` 立即检查一次覆盖该场景）；
- 漏备超 24 小时（``last_backup_at`` 时间戳）：启动/周期检查立即补一次，
  不必等到 ``backup_hour``；补跑后（时间戳刷新为现在）不重复补；
- 旧版状态文件（仅日期无时间戳）：回退每日到点逻辑，不误判漏备；
- 备份异常：吞掉异常、发 ``backup_finished(False, ...)`` 失败信号，当日不重试；
- 成功备份：信号携带 ok=True 与备份服务的 message；
- 状态文件损坏（非法 JSON）：视为当日未备份，正常触发；
- 退出停止：``stop()`` 停掉定时器（应用退出约定，main.py 退出路径调用）；
- ``default_state_path``：LOCALAPPDATA 优先，缺失回退 home 目录。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from workbench.application.auto_backup_scheduler import (
    APP_DATA_DIR_NAME,
    LAST_BACKUP_FILENAME,
    AutoBackupScheduler,
    default_state_path,
)


class _FakeStatus:
    """BackupOperationStatus 桩（鸭子类型：仅依赖 ok/message 属性）。"""

    def __init__(self, ok: bool, message: str = "") -> None:
        self.ok = ok
        self.message = message


class _FakeBackupService:
    """备份服务桩：记录调用次数，返回预设结果或抛出异常。"""

    def __init__(
        self,
        result: object | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls = 0
        self._result = result if result is not None else _FakeStatus(True, "备份完成")
        self._error = error

    def run_manual_backup(self) -> object:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


def _make_scheduler(
    service: _FakeBackupService,
    state_path: Path,
    now: datetime,
) -> AutoBackupScheduler:
    """构造被测调度器：注入 fake 备份回调与 fake clock（状态文件指向 tmp）。"""
    return AutoBackupScheduler(
        service.run_manual_backup,
        state_path=state_path,
        clock=lambda: now,
    )


def test_first_trigger_of_day_runs_backup(tmp_path: Path) -> None:
    """当日首次到点：执行一次备份，状态文件写入当日日期。"""
    service = _FakeBackupService()
    state_path = tmp_path / LAST_BACKUP_FILENAME
    scheduler = _make_scheduler(
        service, state_path, datetime(2026, 9, 1, 3, 0, 0, tzinfo=UTC)
    )

    assert scheduler.check_and_backup() is True
    assert service.calls == 1
    assert "2026-09-01" in state_path.read_text(encoding="utf-8")


def test_repeat_trigger_same_day_skipped(tmp_path: Path) -> None:
    """当日已备份：再次触发（周期检查/重启后）跳过，不重复备份。"""
    service = _FakeBackupService()
    state_path = tmp_path / LAST_BACKUP_FILENAME
    scheduler = _make_scheduler(
        service, state_path, datetime(2026, 9, 1, 3, 0, 0, tzinfo=UTC)
    )

    assert scheduler.check_and_backup() is True
    assert scheduler.check_and_backup() is False
    assert scheduler.check_and_backup() is False
    assert service.calls == 1


def test_before_backup_hour_skipped(tmp_path: Path) -> None:
    """未到 backup_hour 点：即使当日未备份也跳过，且不写状态文件。"""
    service = _FakeBackupService()
    state_path = tmp_path / LAST_BACKUP_FILENAME
    scheduler = _make_scheduler(
        service, state_path, datetime(2026, 9, 1, 2, 59, 0, tzinfo=UTC)
    )

    assert scheduler.check_and_backup() is False
    assert service.calls == 0
    assert not state_path.exists()


def test_next_day_triggers_again(qtbot, tmp_path: Path) -> None:
    """跨天：昨日状态不拦截今日备份（start() 立即检查一次覆盖该场景）。"""
    service = _FakeBackupService()
    state_path = tmp_path / LAST_BACKUP_FILENAME

    scheduler = _make_scheduler(
        service, state_path, datetime(2026, 9, 1, 3, 0, 0, tzinfo=UTC)
    )
    scheduler.start()
    scheduler.stop()
    assert service.calls == 1

    scheduler_next_day = _make_scheduler(
        service, state_path, datetime(2026, 9, 2, 3, 0, 0, tzinfo=UTC)
    )
    assert scheduler_next_day.check_and_backup() is True
    assert service.calls == 2


def test_state_records_timestamp_after_trigger(tmp_path: Path) -> None:
    """触发后状态文件同时记录当日日期与 ISO 时间戳（24h 补跑依据）。"""
    service = _FakeBackupService()
    state_path = tmp_path / LAST_BACKUP_FILENAME
    scheduler = _make_scheduler(
        service, state_path, datetime(2026, 9, 1, 3, 0, 0, tzinfo=UTC)
    )

    assert scheduler.check_and_backup() is True

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["last_backup_date"] == "2026-09-01"
    assert datetime.fromisoformat(payload["last_backup_at"]) == datetime(
        2026, 9, 1, 3, 0, 0, tzinfo=UTC
    )


def test_overdue_catch_up_triggers_before_backup_hour(tmp_path: Path) -> None:
    """漏备超 24h：未到 backup_hour 也立即补跑（昨日 1 点备份、今日 2:30 开机）。"""
    service = _FakeBackupService()
    state_path = tmp_path / LAST_BACKUP_FILENAME
    state_path.write_text(
        json.dumps(
            {
                "last_backup_date": "2026-09-01",
                "last_backup_at": "2026-09-01T01:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    scheduler = _make_scheduler(
        service, state_path, datetime(2026, 9, 2, 2, 30, 0, tzinfo=UTC)
    )

    assert scheduler.check_and_backup() is True
    assert service.calls == 1
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["last_backup_at"] == "2026-09-02T02:30:00+00:00"
    assert payload["last_backup_date"] == "2026-09-02"


def test_within_24h_and_backed_up_today_skipped(tmp_path: Path) -> None:
    """24h 内且当日已备份：既非漏备也非跨日到点，不触发。"""
    service = _FakeBackupService()
    state_path = tmp_path / LAST_BACKUP_FILENAME
    state_path.write_text(
        json.dumps(
            {
                "last_backup_date": "2026-09-02",
                "last_backup_at": "2026-09-02T01:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    scheduler = _make_scheduler(
        service, state_path, datetime(2026, 9, 2, 3, 30, 0, tzinfo=UTC)
    )

    assert scheduler.check_and_backup() is False
    assert service.calls == 0


def test_catch_up_not_repeated_after_refresh(tmp_path: Path) -> None:
    """补跑后时间戳已刷新为现在：周期检查/重启不再重复补。"""
    service = _FakeBackupService()
    state_path = tmp_path / LAST_BACKUP_FILENAME
    state_path.write_text(
        json.dumps(
            {
                "last_backup_date": "2026-09-01",
                "last_backup_at": "2026-08-31T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    scheduler = _make_scheduler(
        service, state_path, datetime(2026, 9, 2, 8, 0, 0, tzinfo=UTC)
    )

    assert scheduler.check_and_backup() is True
    assert service.calls == 1
    assert scheduler.check_and_backup() is False
    assert scheduler.check_and_backup() is False
    assert service.calls == 1


def test_legacy_date_only_state_falls_back_to_daily_logic(tmp_path: Path) -> None:
    """旧版状态文件（仅日期无时间戳）：不误判漏备，仍按每日日期逻辑防重复。"""
    service = _FakeBackupService()
    state_path = tmp_path / LAST_BACKUP_FILENAME
    state_path.write_text(
        json.dumps({"last_backup_date": "2026-09-02"}),
        encoding="utf-8",
    )
    scheduler = _make_scheduler(
        service, state_path, datetime(2026, 9, 2, 8, 0, 0, tzinfo=UTC)
    )

    assert scheduler.check_and_backup() is False
    assert service.calls == 0


def test_backup_failure_emits_signal_and_skips_retry_today(
    qtbot,
    tmp_path: Path,
) -> None:
    """备份异常：吞掉异常、发失败信号；状态仍写入（当日不重试）。"""
    service = _FakeBackupService(error=RuntimeError("磁盘已满"))
    state_path = tmp_path / LAST_BACKUP_FILENAME
    scheduler = _make_scheduler(
        service, state_path, datetime(2026, 9, 1, 3, 0, 0, tzinfo=UTC)
    )

    with qtbot.waitSignal(scheduler.backup_finished) as blocker:
        scheduler.check_and_backup()

    assert list(blocker.args) == [False, "自动备份失败：磁盘已满"]
    assert service.calls == 1
    assert scheduler.check_and_backup() is False
    assert service.calls == 1


def test_backup_success_signal_carries_message(qtbot, tmp_path: Path) -> None:
    """成功备份：信号携带 ok=True 与备份服务的 message。"""
    service = _FakeBackupService(result=_FakeStatus(True, "备份完成：backup-1.db"))
    state_path = tmp_path / LAST_BACKUP_FILENAME
    scheduler = _make_scheduler(
        service, state_path, datetime(2026, 9, 1, 3, 0, 0, tzinfo=UTC)
    )

    with qtbot.waitSignal(scheduler.backup_finished) as blocker:
        scheduler.check_and_backup()

    assert list(blocker.args) == [True, "备份完成：backup-1.db"]


def test_corrupted_state_file_treated_as_not_backed_up(tmp_path: Path) -> None:
    """状态文件损坏（非法 JSON）：视为当日未备份，正常触发。"""
    service = _FakeBackupService()
    state_path = tmp_path / LAST_BACKUP_FILENAME
    state_path.write_text("{not-json", encoding="utf-8")
    scheduler = _make_scheduler(
        service, state_path, datetime(2026, 9, 1, 3, 0, 0, tzinfo=UTC)
    )

    assert scheduler.check_and_backup() is True
    assert service.calls == 1


def test_stop_stops_timer(qtbot, tmp_path: Path) -> None:
    """退出停止：start() 后定时器激活，stop() 后停掉，不再周期触发备份。"""
    service = _FakeBackupService()
    state_path = tmp_path / LAST_BACKUP_FILENAME
    # 时钟固定在未到点时刻：start() 的立即检查不会触发备份，只启动定时器
    scheduler = _make_scheduler(
        service, state_path, datetime(2026, 9, 1, 2, 0, 0, tzinfo=UTC)
    )

    scheduler.start()
    assert scheduler._timer.isActive() is True
    assert service.calls == 0

    scheduler.stop()
    assert scheduler._timer.isActive() is False


def test_default_state_path_prefers_localappdata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """LOCALAPPDATA 存在：状态文件位于 %LOCALAPPDATA%/WarehouseWorkbench 下。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_state_path() == tmp_path / APP_DATA_DIR_NAME / LAST_BACKUP_FILENAME


def test_default_state_path_falls_back_to_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LOCALAPPDATA 缺失：回退 home 目录。"""
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert default_state_path() == Path.home() / APP_DATA_DIR_NAME / LAST_BACKUP_FILENAME
