"""pytest-qt（offscreen）UI 测试：导入向导五步流程（M1 Task 6）。

覆盖 spec"导入向导与错误隔离"的展示层验收：
- 文件选择页：快照载入、文件名/大小/编码信息、编码失败阻断；
- 映射页：默认同名预选、缺必填映射"下一步"禁用并提示、导入类型切换重建字段；
- 预览页：前 20 行原始数据（超出截断）；
- 执行页：同步执行（_run_import 无弹窗）、汇总回显、重复文件检出后禁用前进；
- 错误明细页：五列表格（行号/字段/错误码/原始值/修复建议）来自 import_error；
- 导入入口页与主窗口导航接线。

说明：``QWizard.next()`` 程序化调用不受 ``isComplete()`` 约束（Qt 已知行为，
按钮禁用才是用户侧门禁），故"阻断"断言采用 Next 按钮启用状态。
"""

from __future__ import annotations

import csv
from pathlib import Path

import workbench.presentation.import_page as import_page_module
from local_data.repository import MasterDataRepository
from PySide6.QtWidgets import QDialog, QWizard
from sqlalchemy.orm import Session, sessionmaker
from workbench.application.analysis_usecase import RunAnalysisUseCase
from workbench.application.import_manager import (
    ENCODING_GBK,
    ENCODING_UTF8,
    ERROR_DECIMAL_INVALID,
    ERROR_REQUIRED_MISSING,
    IMPORT_TYPE_EVENTS,
    IMPORT_TYPE_MASTER,
    CsvImportManager,
    DuplicateImportFound,
    ImportRunSummary,
)
from workbench.infrastructure.db.result_store import SqlResultStore
from workbench.infrastructure.engine_adapter.providers import FakeEngineProvider
from workbench.presentation.import_page import ImportPage
from workbench.presentation.import_wizard import (
    ERROR_COLUMNS,
    PREVIEW_ROW_LIMIT,
    ImportWizard,
    suggest_column,
)
from workbench.presentation.main_window import IMPORT_NAV_LABEL, NAV_ITEMS, MainWindow

MASTER_HEADER = ["sku_id", "name", "category", "unit", "unit_cost"]
MASTER_VALID_ROWS = [["SKU-001", "苏打水 330ml", "饮料", "瓶", "2.50"]]
MASTER_MIXED_ROWS = [
    ["SKU-001", "苏打水 330ml", "饮料", "瓶", "2.50"],  # 行 2：合法
    ["", "无名商品", "饮料", "瓶", "3"],  # 行 3：缺 sku_id
    ["SKU-002", "咖啡 250ml", "饮料", "罐", "abc"],  # 行 4：unit_cost 非数值
]
EVENT_HEADER = [
    "event_id",
    "sku_id",
    "warehouse_id",
    "move_type",
    "quantity",
    "occurred_at",
]


def write_master_csv(path: Path, rows: list[list[str]], encoding: str = "utf-8") -> Path:
    """写主数据 CSV（首行为表头）。"""
    with path.open("w", encoding=encoding, newline="") as handle:
        csv.writer(handle).writerows([MASTER_HEADER, *rows])
    return path


def next_button_enabled(wizard: ImportWizard) -> bool:
    """向导"下一步"按钮是否可点（用户侧门禁）。"""
    button = wizard.button(QWizard.WizardButton.NextButton)
    return button is not None and button.isEnabled()


# ---------------------------------------------------------------------------
# 纯逻辑：默认列匹配
# ---------------------------------------------------------------------------


def test_suggest_column_matching() -> None:
    """默认映射：同名精确 → 归一化匹配 → 长字段模糊包含；短字段不做模糊。"""
    assert suggest_column("sku_id", ["sku_id", "name"]) == "sku_id"
    # 归一化：去空白/下划线、忽略大小写
    assert suggest_column("sku_id", ["SKU ID"]) == "SKU ID"
    # 长字段（≥6 字符）允许包含式模糊匹配
    assert suggest_column("warehouse_id", ["仓库 warehouse_id"]) == "仓库 warehouse_id"
    # 短字段不模糊：避免 unit 误配 unit_cost
    assert suggest_column("unit", ["unit_cost"]) is None
    # 无匹配返回 None（留在"不导入"）
    assert suggest_column("industry", ["sku_id", "name"]) is None


# ---------------------------------------------------------------------------
# 第 1 步：文件选择页
# ---------------------------------------------------------------------------


def test_file_select_page_loads_snapshot_and_info(
    qtbot,
    tmp_path: Path,
    import_manager: CsvImportManager,
) -> None:
    """载入文件：快照就绪、信息含文件名/编码/行列数、可进入下一步。"""
    wizard = ImportWizard(import_manager)
    qtbot.addWidget(wizard)

    assert not wizard.file_page.isComplete()  # 未选文件不可前进
    wizard.file_page.set_file(write_master_csv(tmp_path / "skus.csv", MASTER_VALID_ROWS))

    assert wizard.file_page.isComplete()
    assert wizard.snapshot is not None
    assert len(wizard.snapshot.rows) == 1
    info = wizard.file_page.info_label.text()
    assert "skus.csv" in info
    assert f"编码：{ENCODING_UTF8}" in info
    assert "数据行：1 行" in info


def test_file_select_page_gbk_encoding(
    qtbot,
    tmp_path: Path,
    import_manager: CsvImportManager,
) -> None:
    """GBK 样本：编码探测退到 GBK 并展示。"""
    wizard = ImportWizard(import_manager)
    qtbot.addWidget(wizard)

    csv_path = write_master_csv(
        tmp_path / "skus_gbk.csv", MASTER_VALID_ROWS, encoding="gbk"
    )
    wizard.file_page.set_file(csv_path)

    assert wizard.file_page.isComplete()
    assert f"编码：{ENCODING_GBK}" in wizard.file_page.info_label.text()


def test_file_select_page_encoding_failure_blocks(
    qtbot,
    tmp_path: Path,
    import_manager: CsvImportManager,
) -> None:
    """编码探测失败：错误可见、快照为空、"下一步"禁用。"""
    wizard = ImportWizard(import_manager)
    qtbot.addWidget(wizard)
    wizard.show()

    bad_path = tmp_path / "bad.csv"
    bad_path.write_bytes(b"\xff\xff\xff\xff")  # UTF-8 / GBK 均非法
    wizard.file_page.set_file(bad_path)

    assert wizard.snapshot is None
    assert not wizard.file_page.isComplete()
    assert wizard.file_page.error_label.isVisibleTo(wizard.file_page)
    assert wizard.file_page.error_label.text()  # 具体错误信息
    assert not next_button_enabled(wizard)


# ---------------------------------------------------------------------------
# 第 2 步：导入类型与字段映射页
# ---------------------------------------------------------------------------


def test_mapping_page_missing_required_blocks_next(
    qtbot,
    tmp_path: Path,
    import_manager: CsvImportManager,
) -> None:
    """缺必填映射：提示缺哪些字段、"下一步"禁用；补齐映射后恢复。"""
    wizard = ImportWizard(import_manager)
    qtbot.addWidget(wizard)
    wizard.show()

    # 表头缺 name 列（无法自动映射）
    header = ["sku_id", "category", "unit", "unit_cost"]
    csv_path = tmp_path / "skus_no_name.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows([header, ["SKU-001", "饮料", "瓶", "2.5"]])

    wizard.file_page.set_file(csv_path)
    wizard.next()  # 进入映射页
    assert wizard.currentId() == 1

    mapping_page = wizard.mapping_page
    assert not mapping_page.isComplete()
    assert "name" in mapping_page.hint_label.text()
    assert mapping_page.hint_label.isVisibleTo(mapping_page)
    assert not next_button_enabled(wizard)

    # 手动把 name 映射到 category 列：门禁解除
    name_combo = mapping_page.field_combo("name")
    assert name_combo is not None
    name_combo.setCurrentIndex(name_combo.findText("category"))

    assert mapping_page.isComplete()
    assert not mapping_page.hint_label.isVisibleTo(mapping_page)
    assert next_button_enabled(wizard)


def test_mapping_page_rebuilds_on_import_type_switch(
    qtbot,
    tmp_path: Path,
    import_manager: CsvImportManager,
) -> None:
    """切换导入类型：映射字段按主数据/事件契约重建并保持同名预选。"""
    wizard = ImportWizard(import_manager)
    qtbot.addWidget(wizard)
    wizard.show()  # QWizard 需进入首页后 next() 才生效（currentId 不再是 -1）

    csv_path = tmp_path / "events.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(
            [EVENT_HEADER, ["E-001", "SKU-001", "WH-01", "INBOUND", "10", "2026-08-01"]]
        )
    wizard.snapshot = import_manager.read_csv(csv_path)
    wizard.next()  # 进入映射页（默认主数据）

    assert wizard.mapping_page.field_combo("name") is not None

    wizard.mapping_page.type_combo.setCurrentIndex(1)  # 库存事件

    assert wizard.mapping_page.current_import_type() == IMPORT_TYPE_EVENTS
    assert wizard.mapping_page.field_combo("event_id") is not None
    assert wizard.mapping_page.field_combo("name") is None  # 主数据专属字段已移除
    assert wizard.mapping_page.isComplete()  # EVENT_HEADER 同名自动映射

    import_type, mapping = wizard.mapping_page.collect_mapping()
    assert import_type == IMPORT_TYPE_EVENTS
    assert mapping["event_id"] == "event_id"
    assert "name" not in mapping  # 未映射字段不进 mapping


# ---------------------------------------------------------------------------
# 第 3-5 步：预览 / 执行 / 错误明细
# ---------------------------------------------------------------------------


def test_wizard_full_flow_master_import(
    qtbot,
    tmp_path: Path,
    import_manager: CsvImportManager,
    session_factory: sessionmaker[Session],
) -> None:
    """五步全链路：选文件 → 同名映射 → 预览 → 执行 → 错误明细，错误隔离落库。"""
    wizard = ImportWizard(import_manager)
    qtbot.addWidget(wizard)
    wizard.show()  # QWizard 需进入首页后 next() 才生效（currentId 不再是 -1）

    # 第 1 步：选择混合质量主数据文件
    csv_path = write_master_csv(tmp_path / "skus.csv", MASTER_MIXED_ROWS)
    wizard.file_page.set_file(csv_path)

    # 第 2 步：默认同名映射，必填齐全
    wizard.next()
    assert wizard.currentId() == 1
    assert wizard.mapping_page.isComplete()
    import_type, mapping = wizard.mapping_page.collect_mapping()
    assert import_type == IMPORT_TYPE_MASTER
    assert mapping["sku_id"] == "sku_id"
    assert mapping["name"] == "name"

    # 第 3 步：预览原始数据（3 行 5 列）
    wizard.next()
    assert wizard.currentId() == 2
    table = wizard.preview_page.preview_table
    assert table.rowCount() == 3
    assert table.columnCount() == len(MASTER_HEADER)
    assert table.item(0, 0).text() == "SKU-001"
    assert table.horizontalHeaderItem(0).text() == "sku_id"

    # 第 4 步：执行（_run_import 为无弹窗逻辑入口）
    wizard.next()
    assert wizard.currentId() == 3
    assert not wizard.execute_page.isComplete()  # 未执行不可进入错误明细页
    outcome = wizard.execute_page._run_import()
    assert isinstance(outcome, ImportRunSummary)
    assert (outcome.inserted, outcome.error_count) == (1, 2)
    assert wizard.execute_page.isComplete()

    summary_text = wizard.execute_page.summary_label.text()
    assert outcome.batch_id in summary_text
    assert "入库：1" in summary_text
    assert "错误：2" in summary_text

    # 第 5 步：错误明细页（五列表格，数据来自 import_error）
    wizard.next()
    assert wizard.currentId() == 4
    error_table = wizard.error_page.error_table
    assert error_table.rowCount() == 2
    assert error_table.columnCount() == len(ERROR_COLUMNS)
    assert [error_table.horizontalHeaderItem(i).text() for i in range(5)] == list(
        ERROR_COLUMNS
    )
    by_row = {
        int(error_table.item(row, 0).text()): error_table.item(row, 2).text()
        for row in range(error_table.rowCount())
    }
    assert by_row == {3: ERROR_REQUIRED_MISSING, 4: ERROR_DECIMAL_INVALID}
    assert "共 2 条错误行" in wizard.error_page.count_label.text()
    assert error_table.item(0, 4).text()  # 修复建议列非空

    # 合法行入库（UI 全链路后的错误隔离结果）
    sku = MasterDataRepository(session_factory).get_sku_by_sku_id("SKU-001")
    assert sku is not None
    assert sku.name == "苏打水 330ml"


def test_preview_caps_at_20_rows(
    qtbot,
    tmp_path: Path,
    import_manager: CsvImportManager,
) -> None:
    """预览截断：25 行数据仅展示前 20 行。"""
    rows = [[f"SKU-{index:03d}", f"商品 {index}", "饮料", "瓶", "1"] for index in range(25)]
    csv_path = write_master_csv(tmp_path / "skus_many.csv", rows)

    wizard = ImportWizard(import_manager)
    qtbot.addWidget(wizard)
    wizard.show()  # QWizard 需进入首页后 next() 才生效（currentId 不再是 -1）
    wizard.file_page.set_file(csv_path)
    assert len(wizard.snapshot.rows) == 25  # 快照保留全量

    wizard.next()  # 映射页
    wizard.next()  # 预览页
    assert wizard.currentId() == 2
    assert wizard.preview_page.preview_table.rowCount() == PREVIEW_ROW_LIMIT


def test_execute_page_duplicate_file_blocks(
    qtbot,
    tmp_path: Path,
    import_manager: CsvImportManager,
) -> None:
    """重复文件：二次执行返回 DuplicateImportFound，"下一步"禁用、给出批次提示。"""
    wizard = ImportWizard(import_manager)
    qtbot.addWidget(wizard)
    wizard.show()

    csv_path = write_master_csv(tmp_path / "skus.csv", MASTER_VALID_ROWS)
    wizard.file_page.set_file(csv_path)
    wizard.next()  # 映射页
    wizard.next()  # 预览页
    wizard.next()  # 执行页
    assert wizard.currentId() == 3

    first = wizard.execute_page._run_import()
    assert isinstance(first, ImportRunSummary)
    assert wizard.execute_page.isComplete()

    # 重新进入执行页（重置状态）后再次执行同一文件
    wizard.execute_page.initializePage()
    assert not wizard.execute_page.isComplete()

    second = wizard.execute_page._run_import()
    assert isinstance(second, DuplicateImportFound)
    assert second.batch_id == first.batch_id
    assert not wizard.execute_page.isComplete()  # 不能进入错误明细页
    assert not next_button_enabled(wizard)
    message = wizard.execute_page.duplicate_message
    assert message is not None
    assert first.batch_id in message
    assert "已导入过" in message


# ---------------------------------------------------------------------------
# 导入入口页与主窗口接线
# ---------------------------------------------------------------------------


def test_import_page_updates_last_result(
    qtbot,
    monkeypatch,
    tmp_path: Path,
    import_manager: CsvImportManager,
) -> None:
    """向导接受退出后，入口页回显最近一次导入汇总。"""
    csv_path = write_master_csv(tmp_path / "skus.csv", MASTER_VALID_ROWS)
    mapping = {name: name for name in MASTER_HEADER}

    class AutoImportWizard(ImportWizard):
        """测试替身：exec() 直接执行导入并接受退出（绕开模态循环）。"""

        def exec(self) -> int:
            self.import_result = self.manager.run_import(
                path=csv_path, import_type=IMPORT_TYPE_MASTER, mapping=mapping
            )
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(import_page_module, "ImportWizard", AutoImportWizard)

    page = ImportPage(import_manager)
    qtbot.addWidget(page)
    assert page.last_result_label.text() == "尚未导入"

    page.open_button.click()

    text = page.last_result_label.text()
    assert "最近导入" in text
    assert "入库 1" in text
    assert "错误 0" in text


def test_main_window_import_navigation(
    qtbot,
    fake_provider: FakeEngineProvider,
    store: SqlResultStore,
    import_manager: CsvImportManager,
    golden_input: Path,
) -> None:
    """主窗口导航：切到"导入"显示导入入口页（含打开向导按钮）。"""
    window = MainWindow(
        RunAnalysisUseCase(fake_provider, store, golden_input),
        store,
        import_manager=import_manager,
    )
    qtbot.addWidget(window)

    assert window.import_page is not None
    window._nav.setCurrentRow(NAV_ITEMS.index(IMPORT_NAV_LABEL))
    assert window._pages.currentWidget() is window.import_page
    assert window.import_page.open_button.text().startswith("打开导入向导")
