"""Task 3 测试：错误码分布统计（run_import 返回 error_summary + 错误页展示）。

覆盖场景：
- run_import() 返回值包含 error_summary 字段；
- 有错误行时 error_summary 按 error_code 正确分组计数；
- 无错误行时 error_summary 为空字典；
- 错误明细页展示错误码分布统计（中文标签 + 英文码）；
- 无错误行时错误明细页不显示分布统计。
"""

from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker
from workbench.application.import_manager import (
    ERROR_CODE_LABELS,
    ERROR_DECIMAL_INVALID,
    ERROR_MOVE_TYPE_INVALID,
    ERROR_QUANTITY_INVALID,
    ERROR_REQUIRED_MISSING,
    ERROR_SKU_NOT_FOUND,
    ERROR_UNIT_COST_INVALID,
    ERROR_WAREHOUSE_NOT_FOUND,
    IMPORT_TYPE_EVENTS,
    IMPORT_TYPE_MASTER,
    CsvImportManager,
    ImportRunSummary,
)
from workbench.presentation.import_wizard import ImportWizard

MASTER_HEADER = ["sku_id", "name", "category", "unit", "unit_cost"]
EVENT_HEADER = [
    "event_id",
    "sku_id",
    "warehouse_id",
    "move_type",
    "quantity",
    "occurred_at",
]


def write_csv(path: Path, rows: list[list[str]], encoding: str = "utf-8") -> Path:
    """写测试 CSV（首行为表头）。"""
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)
    return path


def identity_mapping(header: list[str]) -> dict[str, str]:
    """同名字段映射（表头即契约字段名）。"""
    return {name: name for name in header}


def seed_master(
    session_factory: sessionmaker[Session],
    *,
    sku_id: str = "SKU-001",
    warehouse_id: str = "WH-01",
) -> None:
    """写入满足事件外键的主数据（SKU + 仓库）。"""
    from local_data.repository import MasterDataRepository

    repo = MasterDataRepository(session_factory)
    repo.add_sku(sku_id=sku_id, name="苏打水 330ml")
    repo.add_warehouse(warehouse_id=warehouse_id, name="一号仓")


# ---------------------------------------------------------------------------
# 纯逻辑：run_import() 返回 error_summary
# ---------------------------------------------------------------------------


def test_error_summary_groups_by_error_code(
    tmp_path: Path,
    import_manager: CsvImportManager,
) -> None:
    """有错误行时 error_summary 按 error_code 正确分组计数。"""
    csv_path = write_csv(
        tmp_path / "skus.csv",
        [
            MASTER_HEADER,
            ["SKU-001", "苏打水 330ml", "饮料", "瓶", "2.50"],  # 行 2：合法
            ["SKU-002", "咖啡 250ml", "饮料", "罐", "-1"],  # 行 3：unit_cost 负
            ["", "无名商品", "饮料", "瓶", "3"],  # 行 4：sku_id 空
            ["SKU-003", "茶 500ml", "饮料", "瓶", "abc"],  # 行 5：unit_cost 非数值
        ],
    )
    summary = import_manager.run_import(
        path=csv_path,
        import_type=IMPORT_TYPE_MASTER,
        mapping=identity_mapping(MASTER_HEADER),
    )

    assert isinstance(summary, ImportRunSummary)
    # error_summary 字段存在且按 error_code 分组计数
    assert summary.error_summary == {
        ERROR_UNIT_COST_INVALID: 1,  # 行 3
        ERROR_REQUIRED_MISSING: 1,  # 行 4
        ERROR_DECIMAL_INVALID: 1,  # 行 5
    }
    assert sum(summary.error_summary.values()) == summary.error_count == 3


def test_error_summary_empty_when_no_errors(
    tmp_path: Path,
    import_manager: CsvImportManager,
) -> None:
    """无错误行时 error_summary 为空字典。"""
    csv_path = write_csv(
        tmp_path / "skus.csv",
        [
            MASTER_HEADER,
            ["SKU-001", "苏打水 330ml", "饮料", "瓶", "2.50"],
            ["SKU-002", "咖啡 250ml", "饮料", "罐", "3.00"],
        ],
    )
    summary = import_manager.run_import(
        path=csv_path,
        import_type=IMPORT_TYPE_MASTER,
        mapping=identity_mapping(MASTER_HEADER),
    )

    assert isinstance(summary, ImportRunSummary)
    assert summary.error_count == 0
    assert summary.error_summary == {}


def test_error_summary_event_import_multiple_codes(
    tmp_path: Path,
    import_manager: CsvImportManager,
    session_factory: sessionmaker[Session],
) -> None:
    """事件导入多种错误码：error_summary 正确分组（SKU/仓库不存在、类型非法、数量非正）。"""
    seed_master(session_factory)
    csv_path = write_csv(
        tmp_path / "events.csv",
        [
            EVENT_HEADER,
            ["E-001", "SKU-001", "WH-01", "INBOUND", "10", "2026-08-01"],  # 行 2：合法
            ["E-002", "SKU-404", "WH-01", "INBOUND", "5", "2026-08-02"],  # 行 3：SKU 不存在
            ["E-003", "SKU-001", "WH-01", "入库", "5", "2026-08-03"],  # 行 4：类型非法
            ["E-004", "SKU-001", "WH-01", "OUTBOUND", "-2", "2026-08-04"],  # 行 5：数量非正
            ["E-005", "SKU-001", "WH-99", "INBOUND", "5", "2026-08-05"],  # 行 6：仓库不存在
        ],
    )
    summary = import_manager.run_import(
        path=csv_path,
        import_type=IMPORT_TYPE_EVENTS,
        mapping=identity_mapping(EVENT_HEADER),
    )

    assert isinstance(summary, ImportRunSummary)
    assert summary.error_summary == {
        ERROR_SKU_NOT_FOUND: 1,
        ERROR_MOVE_TYPE_INVALID: 1,
        ERROR_QUANTITY_INVALID: 1,
        ERROR_WAREHOUSE_NOT_FOUND: 1,
    }
    assert sum(summary.error_summary.values()) == summary.error_count == 4


# ---------------------------------------------------------------------------
# UI：错误明细页展示错误码分布
# ---------------------------------------------------------------------------


def test_error_page_shows_distribution(
    qtbot,
    tmp_path: Path,
    import_manager: CsvImportManager,
) -> None:
    """错误明细页展示错误码分布统计（中文标签 + 英文码）。"""
    csv_path = write_csv(
        tmp_path / "skus.csv",
        [
            MASTER_HEADER,
            ["SKU-001", "苏打水 330ml", "饮料", "瓶", "2.50"],  # 行 2：合法
            ["", "无名商品", "饮料", "瓶", "3"],  # 行 3：缺 sku_id
            ["SKU-002", "咖啡 250ml", "饮料", "罐", "abc"],  # 行 4：unit_cost 非数值
        ],
    )

    wizard = ImportWizard(import_manager)
    qtbot.addWidget(wizard)
    wizard.show()

    # 第 1 步：选择文件
    wizard.file_page.set_file(csv_path)

    # 第 2-4 步：映射（同名自动映射）→ 预览 → 执行
    wizard.next()  # 映射页
    wizard.next()  # 预览页
    wizard.next()  # 执行页

    outcome = wizard.execute_page._run_import()
    assert isinstance(outcome, ImportRunSummary)
    assert outcome.error_summary == {
        ERROR_REQUIRED_MISSING: 1,
        ERROR_DECIMAL_INVALID: 1,
    }

    # 第 5 步：错误明细页
    wizard.next()
    assert wizard.currentId() == 4

    # 错误码分布标签可见且包含中文标签 + 英文码
    label = wizard.error_page.distribution_label
    assert label.isVisibleTo(wizard.error_page)
    text = label.text()
    assert "错误码分布" in text
    assert (
        f"{ERROR_CODE_LABELS[ERROR_REQUIRED_MISSING]}({ERROR_REQUIRED_MISSING}): 1"
        in text
    )
    assert (
        f"{ERROR_CODE_LABELS[ERROR_DECIMAL_INVALID]}({ERROR_DECIMAL_INVALID}): 1"
        in text
    )


def test_error_page_hides_distribution_when_no_errors(
    qtbot,
    tmp_path: Path,
    import_manager: CsvImportManager,
) -> None:
    """无错误行时错误明细页不显示分布统计。"""
    csv_path = write_csv(
        tmp_path / "skus.csv",
        [MASTER_HEADER, ["SKU-001", "苏打水 330ml", "饮料", "瓶", "2.50"]],
    )

    wizard = ImportWizard(import_manager)
    qtbot.addWidget(wizard)
    wizard.show()

    wizard.file_page.set_file(csv_path)
    wizard.next()  # 映射页
    wizard.next()  # 预览页
    wizard.next()  # 执行页

    outcome = wizard.execute_page._run_import()
    assert isinstance(outcome, ImportRunSummary)
    assert outcome.error_summary == {}

    wizard.next()  # 错误明细页
    assert wizard.currentId() == 4

    # 无错误行时分布标签隐藏
    label = wizard.error_page.distribution_label
    assert not label.isVisibleTo(wizard.error_page)
