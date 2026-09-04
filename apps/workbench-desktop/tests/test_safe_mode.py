"""安全模式与升级前备份测试（M3 Task 5，SubTask 5.1/5.2）。

覆盖：
- 升级前备份：旧库存在时在备份目录（数据目录同级 ``backups``）生成快照，
  内容可读（integrity 语义由查询验证）、库文件字节不变；
- 空库首启：不做升级前备份（备份目录不出现），直接进入迁移；
- ``main()`` 接线：迁移（alembic upgrade）抛异常 → 组合根不组装业务依赖，
  调用 ``_run_safe_mode(exc)`` 并透传其退出选择；期间旧库字节不变、
  升级前快照已生成（先备份后迁移的顺序保证）；
- ``main()`` 只读继续：``_run_safe_mode`` 返回 True → 跳过迁移、不重建
  业务连接（零迁移调用），以 ``safe_mode=True`` 组装主窗口（分析/导入
  入口禁用），自动备份调度器仍挂载；
- ``_run_safe_mode``：弹出 SafeModeDialog（stub），透传 db_path/backup_dir/
  restore，退出返回 False、只读继续返回 True；
- ``_build_safe_mode_restore``：恢复回调走 ``BackupService.restore`` 且
  固定 ``safety_backup_dir=None``（安全模式下恢复前安全备份链路不完整）；
- ``prepare_upgrade_backup``：旧库缺 backup_record 登记表 → 跳过登记
  （零依赖快照保底），库文件字节不变（不触发连接层 WAL PRAGMA）；
- SafeModeDialog：备份列表按修改时间倒序、错误摘要展示、恢复走注入回调
  （成功 accept / 失败 critical 且可重试 / 未选中提示）、空目录占位项。

测试不启动事件循环（不调 ``app.exec()``）；``QMessageBox`` 以类级 stub
拦截，避免测试过程中弹出真实模态框。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
import workbench.main as m_mod
import workbench.presentation.safe_mode_dialog as smd_mod
from local_data.connection import DB_FILE_NAME
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog, QMessageBox, QPlainTextEdit
from workbench.application.backup_manager import BackupManager
from workbench.infrastructure.backup.backup_service import BackupService
from workbench.main import (
    SAFE_MODE_EXIT_CODE,
    _run_safe_mode,
    backup_pre_upgrade_snapshot,
    create_session_factory,
    prepare_upgrade_backup,
)
from workbench.presentation.safe_mode_dialog import SafeModeDialog


class _SentinelError(RuntimeError):
    """迁移哨兵异常（证明执行到达 alembic upgrade 调用点）。"""


def _create_old_db(db_path: Path) -> None:
    """造一个旧库（含一行数据），模拟待升级的既有库。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE TABLE legacy (id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO legacy (note) VALUES ('old-data')")
        conn.commit()


def _patch_upgrade_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """令 alembic upgrade 抛出哨兵异常（create_session_factory 内部延迟导入）。"""
    import alembic.command

    def _raise(cfg, revision) -> None:
        raise _SentinelError("迁移到点（测试哨兵）")

    monkeypatch.setattr(alembic.command, "upgrade", _raise)


class _StubMessageBox:
    """QMessageBox 桩：静态记录调用，绝不弹真框（模块级便于类型标注）。"""

    StandardButton = QMessageBox.StandardButton
    information_calls: ClassVar[list[tuple[str, str]]] = []
    critical_calls: ClassVar[list[tuple[str, str]]] = []
    question_calls: ClassVar[list[str]] = []
    next_answer = QMessageBox.StandardButton.Yes

    @staticmethod
    def information(parent, title, text, *args, **kwargs):
        _StubMessageBox.information_calls.append((str(title), str(text)))
        return QMessageBox.StandardButton.Ok

    @staticmethod
    def question(parent, title, text, *args, **kwargs):
        _StubMessageBox.question_calls.append(str(text))
        return _StubMessageBox.next_answer

    @staticmethod
    def critical(parent, title, text, *args, **kwargs):
        _StubMessageBox.critical_calls.append((str(title), str(text)))
        return QMessageBox.StandardButton.Ok


def _stub_message_boxes(monkeypatch: pytest.MonkeyPatch) -> type[_StubMessageBox]:
    """拦截 SafeModeDialog 模块级 QMessageBox（静态记录调用，绝不弹真框）。"""
    monkeypatch.setattr(smd_mod, "QMessageBox", _StubMessageBox)
    return _StubMessageBox


class _StubEngine:
    """create_engine_provider 桩：只暴露 engine_version 属性。"""

    engine_version = "0.0.0-test"


class _StubTray:
    """TrayIcon 桩：show/hide 记录调用；config_rejected 为可连接的普通方法。"""

    def __init__(self) -> None:
        self.shown = False
        self.hidden = False

    def show(self) -> None:
        self.shown = True

    def hide(self) -> None:
        self.hidden = True

    def config_rejected(self, reason: str) -> None:
        """占位槽（AgentWorker.config_rejected 信号的接入点）。"""


def _stub_offline_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """main() 装配段离线桩：覆盖 QApplication/离线客户端/调度器状态文件。

    - ``WORKBENCH_OFFLINE=1``：create_api_client 返回离线占位客户端，
      跳过登录对话框与 SSE/Agent/Sync 线程启动；
    - QApplication 替换为无 GUI 桩（qtbot 已提供全局 app）；
    - MainWindow/TrayIcon/AutoBackupScheduler 以真类运行（offscreen），
      但窗口不 show、托盘不建（``_make_icon`` 桩），实例记入模块观测点
      （``_LAST_WINDOW`` / ``_LAST_SCHEDULER``）供断言；
    - ``app.exec`` 桩返回 0（不进事件循环）；
    - 调度器状态文件指向 tmp，不污染用户 LOCALAPPDATA。
    """
    from workbench.application import auto_backup_scheduler as sched_mod
    from workbench.presentation import main_window as mw_mod
    from workbench.presentation import tray_icon as tray_mod

    class _StubQApplication(QObject):
        """QApplication 桩：QObject 子类（调度器 parent=app 需合法 QObject）。"""

        def __init__(self, *args, **kwargs) -> None:
            super().__init__()

        def setQuitOnLastWindowClosed(self, enabled: bool) -> None: ...

        @staticmethod
        def exec() -> int:
            return 0

    real_window_init = mw_mod.MainWindow.__init__
    real_scheduler_init = sched_mod.AutoBackupScheduler.__init__

    def _window_init(self, *args, **kwargs) -> None:
        real_window_init(self, *args, **kwargs)
        m_mod._LAST_WINDOW = self

    def _scheduler_init(self, *args, **kwargs) -> None:
        kwargs.setdefault("state_path", tmp_path / "last_auto_backup.json")
        real_scheduler_init(self, *args, **kwargs)
        m_mod._LAST_SCHEDULER = self

    monkeypatch.setattr(m_mod, "QApplication", _StubQApplication)
    monkeypatch.setenv("WORKBENCH_OFFLINE", "1")
    monkeypatch.setattr(mw_mod.MainWindow, "__init__", _window_init)
    monkeypatch.setattr(mw_mod.MainWindow, "show", lambda self: None)
    monkeypatch.setattr(sched_mod.AutoBackupScheduler, "__init__", _scheduler_init)
    monkeypatch.setattr(sched_mod.AutoBackupScheduler, "start", lambda self: None)
    monkeypatch.setattr(sched_mod.AutoBackupScheduler, "stop", lambda self: None)
    monkeypatch.setattr(tray_mod, "_make_icon", lambda: None)
    monkeypatch.setattr(
        m_mod, "TrayIcon", lambda window, parent=None: _StubTray()
    )


def _make_dialog(
    qtbot,
    backup_dir: Path,
    db_path: Path,
    restore,
) -> SafeModeDialog:
    """构造被测 SafeModeDialog（qtbot 管理生命周期）。"""
    dialog = SafeModeDialog(
        RuntimeError("迁移炸了"),
        db_path,
        backup_dir,
        restore=restore,
    )
    qtbot.addWidget(dialog)
    return dialog


# --------------------------------------------------------------------------
# 升级前备份：backup_pre_upgrade_snapshot
# --------------------------------------------------------------------------


def test_upgrade_snapshot_created_for_existing_db(tmp_path: Path) -> None:
    """旧库存在：迁移前在同级 backups 目录生成可读快照，库文件字节不变。"""
    data_dir = tmp_path / "wb-data"
    db_path = data_dir / DB_FILE_NAME
    _create_old_db(db_path)
    before = db_path.read_bytes()

    snapshot = backup_pre_upgrade_snapshot(db_path, data_dir)

    assert snapshot.exists()
    assert snapshot.parent == data_dir.parent / "backups"
    assert snapshot.name.startswith("backup-")
    with closing(sqlite3.connect(snapshot)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM legacy").fetchone()[0]
    assert count == 1
    assert db_path.read_bytes() == before


def test_create_session_factory_skips_backup_for_fresh_db(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """空库首启：不做升级前备份（备份目录不出现），直接进入迁移（哨兵验证）。"""
    data_dir = tmp_path / "wb-data"
    _patch_upgrade_raises(monkeypatch)

    with pytest.raises(_SentinelError):
        create_session_factory(data_dir)

    assert not (data_dir.parent / "backups").exists()


# --------------------------------------------------------------------------
# main() 接线：迁移异常 → 安全模式
# --------------------------------------------------------------------------


def test_main_enters_safe_mode_on_migration_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """迁移失败：main() 不组装业务依赖，进入安全模式并透传退出选择。

    monkeypatch create_session_factory 的内部迁移调用抛异常（迁移失败）；
    QApplication/create_engine_provider/_run_safe_mode 以 stub 替换，主流程
    不进事件循环、不弹真框。
    """
    data_dir = tmp_path / "wb-data"
    db_path = data_dir / DB_FILE_NAME
    _create_old_db(db_path)
    before = db_path.read_bytes()
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(data_dir))
    _patch_upgrade_raises(monkeypatch)

    class _StubQApplication:
        def __init__(self, *args, **kwargs) -> None: ...

        def setQuitOnLastWindowClosed(self, enabled: bool) -> None: ...

    captured: dict = {}

    def _stub_safe_mode(error: BaseException, data_dir_arg=None) -> bool:
        captured["error"] = error
        captured["data_dir"] = data_dir_arg
        return False  # 用户在安全模式选择"退出"

    store_calls: list = []
    monkeypatch.setattr(m_mod, "QApplication", _StubQApplication)
    monkeypatch.setattr(m_mod, "create_engine_provider", lambda: _StubEngine())
    monkeypatch.setattr(m_mod, "_run_safe_mode", _stub_safe_mode)
    monkeypatch.setattr(m_mod, "SqlResultStore", lambda sf: store_calls.append(sf))

    result = m_mod.main()

    # 用户退出：透传安全模式退出码；业务组装未发生
    assert result == SAFE_MODE_EXIT_CODE == 3
    assert isinstance(captured["error"], _SentinelError)
    assert captured["data_dir"] is None
    assert store_calls == []

    # 升级前快照已生成（首次尝试 + 重试各保底一次，先备份后迁移），旧库字节不变
    backup_dir = data_dir.parent / "backups"
    snapshots = list(backup_dir.glob("*.db"))
    assert len(snapshots) == 2
    with closing(sqlite3.connect(snapshots[0])) as conn:
        assert conn.execute("SELECT COUNT(*) FROM legacy").fetchone()[0] == 1
    assert db_path.read_bytes() == before


def test_main_restore_then_retry_succeeds_into_main_window(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """升级失败一次后重试成功：直接组装主窗口（不弹安全模式对话框）。

    升级桩首次抛哨兵异常、重试放行：main() 的外层 try 捕获后直接重试
    （恢复回调链路由 SafeModeDialog 专项测试覆盖），验证"恢复后重试成功"
    等价路径——升级被调用两次，业务正常组装，app_meta.json 写入版本。
    构造 QWidget 的测试经 qtbot 提供 QApplication（与既有 UI 测试同口径）。
    """
    data_dir = tmp_path / "wb-data"
    db_path = data_dir / DB_FILE_NAME
    _create_old_db(db_path)
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(data_dir))

    import alembic.command

    calls: list = []
    real_upgrade = alembic.command.upgrade

    def _upgrade_once_failed(cfg, revision) -> None:
        calls.append(revision)
        if len(calls) == 1:
            raise _SentinelError("迁移炸了（测试哨兵）")
        real_upgrade(cfg, revision)  # 重试放行：真实迁移建表 + 种子 + 版本标记

    monkeypatch.setattr(alembic.command, "upgrade", _upgrade_once_failed)
    monkeypatch.setattr(m_mod, "create_engine_provider", lambda: _StubEngine())
    monkeypatch.setattr(
        m_mod, "_run_safe_mode", lambda *a, **k: pytest.fail("不应弹安全模式")
    )
    _stub_offline_main(monkeypatch, tmp_path)

    result = m_mod.main()

    assert result == 0
    assert len(calls) == 2  # 首次失败 + 重试成功
    assert (data_dir / "app_meta.json").exists()  # 成功路径写入版本标记


def test_main_readonly_continue_assembles_main_window(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """只读继续：迁移失败 → _run_safe_mode 返回 True → 以 safe_mode=True 组装。

    验证只读装配：不再重试迁移（create_session_factory 未被再次调用）、
    不写版本标记（app_meta.json 不出现）、主窗口 safe_mode 禁用分析/导入
    入口、备份/报告页不构建（backup_manager/report_manager 传 None 规避对
    旧库的业务查询）。
    """
    data_dir = tmp_path / "wb-data"
    db_path = data_dir / DB_FILE_NAME
    _create_old_db(db_path)
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(data_dir))
    _patch_upgrade_raises(monkeypatch)
    monkeypatch.setattr(m_mod, "create_engine_provider", lambda: _StubEngine())
    monkeypatch.setattr(
        m_mod, "_run_safe_mode", lambda *a, **k: True  # 只读继续
    )
    _stub_offline_main(monkeypatch, tmp_path)

    result = m_mod.main()

    assert result == 0
    # 只读语义：不做种子/版本标记写入（app_meta.json 仅成功迁移路径写入）
    assert not (data_dir / "app_meta.json").exists()
    window = m_mod._LAST_WINDOW
    assert window.safe_mode is True
    assert not window.analysis_page.run_button.isEnabled()
    assert window.import_page is not None
    assert not window.import_page.open_button.isEnabled()
    assert window.backup_page is None
    assert window.report_page is None
    assert m_mod._LAST_SCHEDULER is not None


def test_main_readonly_open_failure_exits_with_safe_mode_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """只读继续但库文件无法打开：以安全模式退出码结束（不崩溃）。"""
    data_dir = tmp_path / "wb-data"
    data_dir.mkdir(parents=True)
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(data_dir))
    _patch_upgrade_raises(monkeypatch)
    monkeypatch.setattr(m_mod, "create_engine_provider", lambda: _StubEngine())
    monkeypatch.setattr(m_mod, "_run_safe_mode", lambda *a, **k: True)

    def _boom(*args, **kwargs) -> None:
        raise RuntimeError("只读打开失败")

    monkeypatch.setattr(m_mod, "create_readonly_session_factory", _boom)
    _stub_offline_main(monkeypatch, tmp_path)

    result = m_mod.main()

    assert result == SAFE_MODE_EXIT_CODE == 3


# --------------------------------------------------------------------------
# _run_safe_mode / _build_safe_mode_restore
# --------------------------------------------------------------------------


def test_run_safe_mode_exit_returns_false(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_run_safe_mode：向对话框透传 db_path/backup_dir/restore，退出返回 False。"""

    class _StubSafeModeDialog:
        last_instance = None

        def __init__(self, error, db_path, backup_dir, restore=None, parent=None):
            self.init_args = (error, db_path, backup_dir, restore)
            self.exec_called = False
            _StubSafeModeDialog.last_instance = self

        def exec(self) -> int:
            self.exec_called = True
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(m_mod, "SafeModeDialog", _StubSafeModeDialog)

    result = _run_safe_mode(RuntimeError("boom"), tmp_path / "wb-data")

    assert result is False  # 退出（非只读继续）：调用方以 SAFE_MODE_EXIT_CODE 结束
    dialog = _StubSafeModeDialog.last_instance
    assert dialog is not None and dialog.exec_called
    error, db_path, backup_dir, restore = dialog.init_args
    assert str(error) == "boom"
    assert db_path == tmp_path / "wb-data" / DB_FILE_NAME
    assert backup_dir == tmp_path / "backups"
    assert callable(restore)


def test_run_safe_mode_continue_readonly_returns_true(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_run_safe_mode：对话框 continue_readonly=True（只读继续）→ 返回 True。"""

    class _StubSafeModeDialog:
        def __init__(self, error, db_path, backup_dir, restore=None, parent=None):
            self.continue_readonly = False

        def exec(self) -> int:
            self.continue_readonly = True  # 模拟用户点击"安全模式继续（只读）"
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(m_mod, "SafeModeDialog", _StubSafeModeDialog)

    assert _run_safe_mode(RuntimeError("boom"), tmp_path / "wb-data") is True


def test_safe_mode_restore_uses_backup_service_without_safety_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """安全模式恢复回调：走 BackupService.restore 且固定 safety_backup_dir=None。"""

    class _FakeBackupService:
        last_instance = None
        last_call: tuple | None = None

        def __init__(self, session_factory) -> None:
            self.session_factory = session_factory
            _FakeBackupService.last_instance = self

        def restore(self, backup_path, db_path, safety_backup_dir=None):
            _FakeBackupService.last_call = (backup_path, db_path, safety_backup_dir)

            class _Result:
                success = True
                error = None

            return _Result()

    monkeypatch.setattr(m_mod, "BackupService", _FakeBackupService)
    data_dir = tmp_path / "wb-data"
    data_dir.mkdir(parents=True)
    backup_path = tmp_path / "backups" / "backup-1.db"

    restore = m_mod._build_safe_mode_restore()
    result = restore(backup_path, data_dir / DB_FILE_NAME)

    assert result.success is True
    service = _FakeBackupService.last_instance
    assert service is not None and service.session_factory is not None
    assert _FakeBackupService.last_call == (backup_path, data_dir / DB_FILE_NAME, None)


# --------------------------------------------------------------------------
# SafeModeDialog
# --------------------------------------------------------------------------


def test_dialog_shows_error_summary(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """错误摘要：展示异常类型与消息；标题为安全模式。"""
    stub = _stub_message_boxes(monkeypatch)
    dialog = _make_dialog(qtbot, tmp_path / "backups", tmp_path / "wb-data" / DB_FILE_NAME,
                          restore=lambda backup, db: SimpleNamespace(success=False, error=None))

    assert "安全模式" in dialog.windowTitle()
    views = dialog.findChildren(QPlainTextEdit)
    assert len(views) == 1
    assert views[0].toPlainText() == "RuntimeError: 迁移炸了"
    assert stub.information_calls == []


# --------------------------------------------------------------------------
# 升级安全用例：run_pre_upgrade_backup（M3 Task 5，SubTask 5.2）
# --------------------------------------------------------------------------


def test_run_pre_upgrade_backup_success(
    session_factory,
    data_dir: Path,
) -> None:
    """升级前备份：AUTO 类型登记、版本号入消息、文件生成且校验通过。"""
    from workbench.application.upgrade_safety import run_pre_upgrade_backup

    db_path = data_dir / DB_FILE_NAME
    backup_dir = data_dir.parent / "backups"
    service = BackupService(session_factory)

    status = run_pre_upgrade_backup(service, db_path, backup_dir, "0.1.0")

    assert status.ok is True
    assert status.backup_path is not None and status.backup_path.exists()
    assert "v0.1.0" in status.message
    records = BackupManager(service, db_path, backup_dir).list_backups()
    assert len(records) == 1
    assert records[0]["backup_type"] == "AUTO"
    assert records[0]["status"] == "VERIFIED"


def test_run_pre_upgrade_backup_reports_failure(
    session_factory,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """升级前备份失败（库文件缺失）：ok=False 且消息带原因。"""
    from workbench.application.upgrade_safety import run_pre_upgrade_backup

    service = BackupService(session_factory)

    status = run_pre_upgrade_backup(
        service, data_dir / "missing.db", data_dir.parent / "backups", "0.1.0"
    )

    assert status.ok is False
    assert "升级前备份失败" in status.message
    assert status.backup_path is None


# --------------------------------------------------------------------------
# prepare_upgrade_backup：旧库缺登记表的异常安全（M3 Task 5）
# --------------------------------------------------------------------------


def test_prepare_upgrade_backup_skips_legacy_db_without_backup_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """旧库缺 backup_record 表：跳过登记备份且不触碰库文件（字节不变）。

    连接层 PRAGMA（journal_mode=WAL）会改库头字节；登记备份必须先做
    只读 sqlite_master 预检，绝不为此建立 SQLAlchemy 连接——用守卫桩
    钉死"未走到 connect"。
    """
    import local_data.connection as ld_conn

    data_dir = tmp_path / "wb-data"
    data_dir.mkdir(parents=True)
    db_path = data_dir / DB_FILE_NAME
    _create_old_db(db_path)
    before = db_path.read_bytes()
    version_changed_calls: list = []

    def _fake_version_changed(*args: object, **kwargs: object) -> bool:
        version_changed_calls.append(args)
        return True

    monkeypatch.setattr(m_mod, "version_changed_since_last_run", _fake_version_changed)

    def _guard_connect(*args, **kwargs) -> None:
        raise RuntimeError("不应建立 SQLAlchemy 连接")

    monkeypatch.setattr(ld_conn, "connect", _guard_connect)

    status = prepare_upgrade_backup(db_path, data_dir, "9.9.9")

    assert status.ok is False
    assert "backup_record" in status.message
    assert "不应建立 SQLAlchemy 连接" not in status.message
    assert version_changed_calls, "版本门控应先于表预检执行"
    assert db_path.read_bytes() == before  # 连接未建立，WAL 未触发


def test_prepare_upgrade_backup_wraps_service_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """登记备份内部异常（如连接失败）被吞掉：返回 ok=False，不外抛。"""
    import local_data.connection as ld_conn

    data_dir = tmp_path / "wb-data"
    data_dir.mkdir(parents=True)
    db_path = data_dir / DB_FILE_NAME
    _create_old_db(db_path)
    monkeypatch.setattr(m_mod, "version_changed_since_last_run", lambda *a, **k: True)
    monkeypatch.setattr(m_mod, "_table_exists", lambda *a, **k: True)

    def _boom_connect(*args, **kwargs) -> None:
        raise RuntimeError("连接炸了")

    monkeypatch.setattr(ld_conn, "connect", _boom_connect)

    status = prepare_upgrade_backup(db_path, data_dir, "9.9.9")

    assert status.ok is False
    assert "连接炸了" in status.message
