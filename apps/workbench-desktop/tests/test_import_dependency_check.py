"""导入向导前置依赖检测测试（Task 2）：库存事件导入前检测 SKU 与仓库主数据。

- 空库时导入库存事件被阻断并显示正确提示；
- 有 SKU 和仓库数据时正常放行（不弹出提示）。

使用 pytest-qt offscreen 模式，tmp_path 隔离数据库。
"""

from __future__ import annotations

import csv
from pathlib import Path

from local_data.repository import MasterDataRepository
from PySide6.QtWidgets import QWizard
from sqlalchemy.orm import Session, sessionmaker
from workbench.application.import_manager import (
    IMPORT_TYPE_EVENTS,
    ImportRunSummary,
)
from workbench.presentation.import_wizard import ImportWizard

EVENT_HEADER = [
    "event_id",
    "sku_id",
    "warehouse_id",
    "move_type",
    "quantity",
    "occurred_at",
]
EVENT_VALID_ROWS = [["EVT-001", "SKU-001", "WH-01", "INBOUND", "10", "2026-08-01"]]


def write_event_csv(path: Path) -> Path:
    """写库存事件 CSV（首行为表头）。"""
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows([EVENT_HEADER, *EVENT_VALID_ROWS])
    return path


def test_event_import_blocked_when_db_empty(
    qtbot,
    tmp_path: Path,
    import_manager,
) -> None:
    """空库时导入库存事件被阻断：SKU 表为空，显示前置依赖提示。"""
    wizard = ImportWizard(import_manager)
    qtbot.addWidget(wizard)
    wizard.show()

    wizard.file_page.set_file(write_event_csv(tmp_path / "events.csv"))

    # 进入映射页，切换为库存事件类型
    wizard.next()
    wizard.mapping_page.type_combo.setCurrentIndex(1)
    assert wizard.mapping_page.current_import_type() == IMPORT_TYPE_EVENTS
    assert wizard.mapping_page.isComplete()

    # 进入预览页 → 执行页
    wizard.next()
    wizard.next()
    assert wizard.currentId() == 3

    # 执行导入：空库应被阻断，返回 None 并设置失败提示
    outcome = wizard.execute_page._run_import()
    assert outcome is None
    failure = wizard.execute_page._failure_message
    assert failure is not None
    assert "请先导入 SKU 主数据后再导入库存事件" in failure

    # 未创建导入批次（import_result 仍为 None）
    assert wizard.import_result is None
    # "下一步"按钮禁用（不能进入错误明细页）
    next_button = wizard.button(QWizard.WizardButton.NextButton)
    assert next_button is not None and not next_button.isEnabled()


def test_event_import_allowed_when_master_data_exists(
    qtbot,
    tmp_path: Path,
    import_manager,
    session_factory: sessionmaker[Session],
) -> None:
    """有 SKU 和仓库数据时正常放行（不弹出提示）。"""
    # 预置主数据：1 SKU + 1 仓库
    repo = MasterDataRepository(session_factory)
    repo.add_sku(sku_id="SKU-001", name="测试商品")
    repo.add_warehouse(warehouse_id="WH-01", name="主仓")

    wizard = ImportWizard(import_manager)
    qtbot.addWidget(wizard)
    wizard.show()

    wizard.file_page.set_file(write_event_csv(tmp_path / "events.csv"))

    wizard.next()
    wizard.mapping_page.type_combo.setCurrentIndex(1)
    assert wizard.mapping_page.isComplete()

    wizard.next()
    wizard.next()
    assert wizard.currentId() == 3

    # 执行导入：主数据齐全，应正常放行
    outcome = wizard.execute_page._run_import()
    assert isinstance(outcome, ImportRunSummary)
    assert outcome.completed
    assert outcome.error_count == 0
    # 无失败提示（_execute 不会弹出 QMessageBox）
    assert wizard.execute_page._failure_message is None
