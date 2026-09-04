"""备份与恢复测试（M1 Task 9.5）：备份服务 / 用例 / 备份页。

覆盖 spec“备份与恢复安全”验收场景（§35.9 核心不变量：操作绝不损坏当前库）：
- 手动备份：VACUUM INTO 全量快照 + SHA-256 登记 + backup_record
  （CREATED → VERIFIED，verified_at 非空，MANUAL 类型）；
- 恢复：先校验后原子替换、恢复前自动安全备份当前库（AUTO）、
  关键表行数守恒（回到备份时点，多出的数据消失）；
- 失败零改动（两路）：
  * 已登记备份被篡改（SHA-256 与登记值不一致）→ 拒绝恢复，当前库字节不变；
  * 未登记且损坏的文件（无法作为数据库打开/完整性校验失败）→ 拒绝恢复，
    当前库字节不变；
- 备份页：手动备份回显与列表刷新、恢复二次确认（取消/确认）；
- 主窗口导航接线。
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest
from contracts import AnalysisResult, InputSummary, ResultMetric
from local_data.connection import DB_FILE_NAME
from local_data.models import (
    BACKUP_STATUS_FAILED,
    BACKUP_STATUS_VERIFIED,
    BACKUP_TYPE_AUTO,
    BACKUP_TYPE_MANUAL,
)
from PySide6.QtWidgets import QMessageBox
from sqlalchemy.orm import Session, sessionmaker
from workbench.application.analysis_usecase import RunAnalysisUseCase
from workbench.application.backup_manager import BackupManager
from workbench.infrastructure.backup.backup_service import BackupService
from workbench.infrastructure.db.result_store import SqlResultStore
from workbench.infrastructure.engine_adapter.providers import FakeEngineProvider
from workbench.presentation.backup_page import BackupPage
from workbench.presentation.main_window import NAV_ITEMS, MainWindow


def _make_result(run_id: str) -> AnalysisResult:
    """构造最小可用 AnalysisResult（数据守恒断言用）。"""
    return AnalysisResult(
        schema_version="1.0",
        run_id=run_id,
        engine_version="0.1.0-fake",
        formula_version="0.1.0",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        metrics=[
            ResultMetric(
                name="KPI.OUTBOUND_QTY",
                value="60.5",
                unit="件",
                formula_id="F-KPI-003",
                formula_version="0.1.0",
                sample_count=3,
            )
        ],
        warnings=[],
        summary="备份恢复测试",
        input_summary=InputSummary(
            sku_count=1,
            movement_count=1,
            snapshot_count=0,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            dataset_digest="0" * 64,
        ),
    )


def _sha256_file(path: Path) -> str:
    """计算文件 SHA-256（当前库零改动断言用）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture
def db_path(data_dir: Path) -> Path:
    """当前库文件路径（conftest 已迁移到 head 的 warehouse.db）。"""
    return data_dir / DB_FILE_NAME


@pytest.fixture
def backup_dir(tmp_path: Path) -> Path:
    """备份输出目录（tmp 重定向，绝不写真实 %LOCALAPPDATA%）。"""
    return tmp_path / "backups"


@pytest.fixture
def service(session_factory: sessionmaker[Session]) -> BackupService:
    return BackupService(session_factory)


@pytest.fixture
def manager(
    session_factory: sessionmaker[Session], db_path: Path, backup_dir: Path
) -> BackupManager:
    return BackupManager(BackupService(session_factory), db_path, backup_dir)


# ---------------------------------------------------------------------------
# 备份（VACUUM INTO + 读回复验）
# ---------------------------------------------------------------------------


def test_backup_creates_verified_record(
    service: BackupService,
    store: SqlResultStore,
    db_path: Path,
    backup_dir: Path,
) -> None:
    """手动备份：VERIFIED、SHA-256 匹配、verified_at 非空、关键表行数可读。"""
    store.save(_make_result("run-backup-0001"))

    result = service.backup(db_path, backup_dir)
    assert result.status == BACKUP_STATUS_VERIFIED
    assert result.file_path.exists()
    assert result.file_path.parent == backup_dir
    assert result.sha256 == _sha256_file(result.file_path)
    assert result.db_schema_version == "local-0007"
    assert result.row_counts.get("analysis_run") == 1

    record = service.get_backup(result.backup_id)
    assert record is not None
    assert record.status == BACKUP_STATUS_VERIFIED
    assert record.verified_at is not None
    assert record.backup_type == BACKUP_TYPE_MANUAL
    assert record.sha256 == result.sha256
    assert [r.backup_id for r in service.list_backups()] == [result.backup_id]


def test_backup_missing_db_records_failure(
    service: BackupService, tmp_path: Path, backup_dir: Path
) -> None:
    """当前库路径不存在：FAILED 记录 + 中文错误（不崩溃）。"""
    result = service.backup(tmp_path / "no-such.db", backup_dir)
    assert result.status == BACKUP_STATUS_FAILED
    assert result.error is not None and "不存在" in result.error
    record = service.get_backup(result.backup_id)
    assert record is not None and record.status == BACKUP_STATUS_FAILED


# ---------------------------------------------------------------------------
# 恢复：成功路径（先校验后原子替换 + 恢复前安全备份）
# ---------------------------------------------------------------------------


def test_restore_roundtrip_with_safety_backup(
    service: BackupService,
    store: SqlResultStore,
    db_path: Path,
    backup_dir: Path,
) -> None:
    """恢复回备份时点：多出的 run 消失；恢复前自动安全备份当前库（AUTO）。"""
    store.save(_make_result("run-keep-0001"))
    backup = service.backup(db_path, backup_dir)

    # 篡改当前库：备份之后新增一个 run
    store.save(_make_result("run-extra-0002"))
    assert store.get("run-extra-0002") is not None

    result = service.restore(backup.file_path, db_path, safety_backup_dir=backup_dir)
    assert result.success
    assert result.error is None

    # 恢复后：备份时点数据在，备份之后新增的数据消失（行数守恒）
    assert store.get("run-keep-0001") is not None
    assert store.get("run-extra-0002") is None

    # 恢复前自动安全备份当前库（AUTO 类型、VERIFIED，包含篡改后状态）
    assert result.safety_backup is not None
    assert result.safety_backup.status == BACKUP_STATUS_VERIFIED
    safety_record = service.get_backup(result.safety_backup.backup_id)
    assert safety_record is not None
    assert safety_record.backup_type == BACKUP_TYPE_AUTO


def test_restore_without_safety_dir(
    service: BackupService,
    store: SqlResultStore,
    db_path: Path,
    backup_dir: Path,
) -> None:
    """不传 safety_backup_dir：直接替换，不产生 AUTO 备份。"""
    store.save(_make_result("run-keep-0001"))
    backup = service.backup(db_path, backup_dir)
    store.save(_make_result("run-extra-0002"))

    result = service.restore(backup.file_path, db_path)
    assert result.success
    assert result.safety_backup is None
    assert store.get("run-extra-0002") is None


# ---------------------------------------------------------------------------
# 恢复：失败零改动（核心不变量）
# ---------------------------------------------------------------------------


def test_restore_rejects_tampered_registered_backup(
    service: BackupService,
    store: SqlResultStore,
    db_path: Path,
    backup_dir: Path,
) -> None:
    """已登记备份被篡改（SHA-256 不一致）：拒绝恢复，当前库字节零改动。"""
    store.save(_make_result("run-keep-0001"))
    backup = service.backup(db_path, backup_dir)
    before = _sha256_file(db_path)

    # 篡改备份文件（截断一半）
    data = backup.file_path.read_bytes()
    backup.file_path.write_bytes(data[: len(data) // 2])

    result = service.restore(backup.file_path, db_path, safety_backup_dir=backup_dir)
    assert result.success is False
    assert result.error is not None
    assert "SHA-256" in result.error  # 校验值比对失败
    assert _sha256_file(db_path) == before  # 当前库字节零改动
    assert store.get("run-keep-0001") is not None  # 数据仍可读


def test_restore_rejects_unregistered_corrupt_file(
    service: BackupService,
    store: SqlResultStore,
    db_path: Path,
    backup_dir: Path,
    tmp_path: Path,
) -> None:
    """未登记且损坏的备份文件：完整性校验失败，拒绝恢复，当前库零改动。"""
    store.save(_make_result("run-keep-0001"))
    backup = service.backup(db_path, backup_dir)
    before = _sha256_file(db_path)

    corrupt = tmp_path / "corrupt.db"
    data = backup.file_path.read_bytes()
    corrupt.write_bytes(data[: len(data) // 2])

    result = service.restore(corrupt, db_path)
    assert result.success is False
    assert result.error is not None
    assert "未做任何改动" in result.error
    assert _sha256_file(db_path) == before


def test_restore_missing_paths(
    service: BackupService,
    store: SqlResultStore,
    db_path: Path,
    backup_dir: Path,
    tmp_path: Path,
) -> None:
    """备份文件不存在 / 当前库不存在：中文错误，零改动。"""
    store.save(_make_result("run-keep-0001"))
    missing_backup = service.restore(tmp_path / "no-such-backup.db", db_path)
    assert missing_backup.success is False
    assert missing_backup.error is not None and "备份文件不存在" in missing_backup.error

    # 备份文件有效但目标库路径不存在（先备份出一分有效文件再指向空路径）
    backup = service.backup(db_path, backup_dir)
    missing_db = service.restore(backup.file_path, tmp_path / "no-db" / "warehouse.db")
    assert missing_db.success is False
    assert missing_db.error is not None and "当前数据库不存在" in missing_db.error


# ---------------------------------------------------------------------------
# BackupManager：状态消息与列表
# ---------------------------------------------------------------------------


def test_manager_manual_backup_and_list(
    manager: BackupManager, store: SqlResultStore, backup_dir: Path
) -> None:
    """手动备份成功消息 + 列表（时间/类型/大小/状态字段齐备）。"""
    store.save(_make_result("run-mgr-0001"))

    status = manager.run_manual_backup()
    assert status.ok is True
    assert "备份完成" in status.message
    assert str(backup_dir) in status.message

    rows = manager.list_backups()
    assert len(rows) == 1
    assert rows[0]["backup_type"] == BACKUP_TYPE_MANUAL
    assert rows[0]["status"] == BACKUP_STATUS_VERIFIED
    assert rows[0]["size_bytes"] > 0
    assert rows[0]["verified_at"] is not None
    assert manager.backup_dir == backup_dir


def test_manager_restore_unknown_id_and_missing_file(
    manager: BackupManager,
    store: SqlResultStore,
    db_path: Path,
    backup_dir: Path,
) -> None:
    """恢复未知 backup_id / 备份文件已被删除：中文错误消息。"""
    unknown = manager.restore("bak-not-exist")
    assert unknown.ok is False
    assert "未找到备份记录" in unknown.message

    store.save(_make_result("run-mgr-0002"))
    service = BackupService.__new__(BackupService)  # 仅取一条已验证备份
    del service  # 不走捷径：用 manager 自身流程
    status_ok = manager.run_manual_backup()
    assert status_ok.ok is True
    backup_id = manager.list_backups()[0]["backup_id"]
    record = manager.get_backup(backup_id)
    assert record is not None
    Path(record.file_path).unlink()  # 备份文件被外部删除

    status = manager.restore(backup_id)
    assert status.ok is False
    assert "备份文件不存在" in status.message


def test_manager_restore_roundtrip(
    manager: BackupManager, store: SqlResultStore
) -> None:
    """用例层完整闭环：备份 → 篡改 → 恢复 → 行数守恒 + 成功消息。"""
    store.save(_make_result("run-mgr-keep-0001"))
    assert manager.run_manual_backup().ok is True
    backup_id = manager.list_backups()[0]["backup_id"]

    store.save(_make_result("run-mgr-extra-0002"))
    status = manager.restore(backup_id)
    assert status.ok is True
    assert "恢复完成" in status.message
    assert "安全备份" in status.message  # 恢复前自动安全备份说明

    assert store.get("run-mgr-keep-0001") is not None
    assert store.get("run-mgr-extra-0002") is None


# ---------------------------------------------------------------------------
# BackupPage（pytest-qt offscreen）
# ---------------------------------------------------------------------------


def test_backup_page_manual_backup_and_list(
    qtbot, manager: BackupManager, store: SqlResultStore
) -> None:
    """备份页：手动备份 → 状态回显 + 列表刷新（编号/类型/状态列）。"""
    store.save(_make_result("run-ui-0001"))
    page = BackupPage(manager)
    qtbot.addWidget(page)

    assert page.backup_table.rowCount() == 0  # 初始无备份
    page.backup_button.click()

    assert "备份完成" in page.status_label.text()
    assert page.backup_table.rowCount() == 1
    assert page.backup_table.item(0, 0).text().startswith("bak-")
    assert page.backup_table.item(0, 2).text() == "手动"  # MANUAL 类型中文展示
    assert page.backup_table.item(0, 4).text() == BACKUP_STATUS_VERIFIED


def test_backup_page_restore_confirmed_and_cancelled(
    qtbot, monkeypatch, manager: BackupManager, store: SqlResultStore
) -> None:
    """备份页恢复：二次确认取消 → “已取消”；确认 → 恢复完成并回到备份时点。"""
    store.save(_make_result("run-ui-keep-0001"))
    page = BackupPage(manager)
    qtbot.addWidget(page)
    page.backup_button.click()
    page.backup_table.selectRow(0)
    backup_id = page.backup_table.item(0, 0).text()

    # 取消恢复
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No),
    )
    page.restore_button.click()
    assert "已取消恢复" in page.status_label.text()

    # 确认恢复（备份后再篡改当前库）
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    store.save(_make_result("run-ui-extra-0002"))
    page.restore_button.click()

    assert "恢复完成" in page.status_label.text()
    assert store.get("run-ui-keep-0001") is not None
    assert store.get("run-ui-extra-0002") is None
    assert page.backup_table.item(0, 0).text() != backup_id  # AUTO 安全备份列首行


def test_backup_page_restore_without_selection(
    qtbot, monkeypatch, manager: BackupManager, store: SqlResultStore
) -> None:
    """未选中备份点恢复：提示先选择，不触发确认框。"""
    store.save(_make_result("run-ui-0001"))
    page = BackupPage(manager)
    qtbot.addWidget(page)
    page.backup_button.click()  # 有备份记录但未选中

    def _unexpected(*args, **kwargs):  # pragma: no cover - 断言不该走到
        raise AssertionError("未选中时不应弹出确认框")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_unexpected))
    page.restore_button.click()
    assert "请先在列表中选择" in page.status_label.text()


def test_main_window_wires_backup_page(
    qtbot,
    fake_provider: FakeEngineProvider,
    store: SqlResultStore,
    session_factory: sessionmaker[Session],
    db_path: Path,
    backup_dir: Path,
    golden_input: Path,
) -> None:
    """主窗口：注入 backup_manager 后“备份”导航挂真实备份页（非占位）。"""
    manager = BackupManager(BackupService(session_factory), db_path, backup_dir)
    window = MainWindow(
        RunAnalysisUseCase(fake_provider, store, golden_input),
        store,
        backup_manager=manager,
    )
    qtbot.addWidget(window)

    assert window.backup_page is not None
    assert window._pages.widget(NAV_ITEMS.index("备份")) is window.backup_page
