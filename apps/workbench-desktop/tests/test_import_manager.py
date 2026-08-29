"""Task 5 测试：CSV 导入用例（纯逻辑，不依赖 Qt）。

覆盖 spec"导入向导与错误隔离"场景：
- 混合质量 CSV：合法行入库 + 非法行进 import_error 且五要素齐全（错误隔离）；
- 重复 file_hash 阻断（不静默重复入库）；
- GBK 编码样本（探测与中文内容往返）；
- 事件导入错误隔离（错误行不影响合法行）与 SKU 存在性校验；
- 事件导入后余额投影已更新（BalanceProjectionService 重建）；
- 缺必填列阻断（不创建批次）；
- 事件幂等重导入的跳过计数；全部行失败置 FAILED；编码探测失败。
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest
from local_data.models import (
    IMPORT_STATUS_COMPLETED,
    IMPORT_STATUS_FAILED,
    ImportBatchRow,
)
from local_data.projection import BalanceProjectionService
from local_data.repository import MasterDataRepository
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from workbench.application.import_manager import (
    ENCODING_GBK,
    ERROR_DECIMAL_INVALID,
    ERROR_ENCODING,
    ERROR_MISSING_COLUMN,
    ERROR_MOVE_TYPE_INVALID,
    ERROR_QUANTITY_INVALID,
    ERROR_REQUIRED_MISSING,
    ERROR_SKU_DUPLICATE,
    ERROR_SKU_NOT_FOUND,
    ERROR_UNIT_COST_INVALID,
    IMPORT_TYPE_EVENTS,
    IMPORT_TYPE_MASTER,
    CsvImportError,
    CsvImportManager,
    DuplicateImportFound,
    ImportRunSummary,
    detect_encoding,
)

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


def batch_count(session_factory: sessionmaker[Session]) -> int:
    """import_batch 行数（验证批次创建/不创建）。"""
    with session_factory() as session:
        return int(
            session.execute(select(func.count()).select_from(ImportBatchRow)).scalar_one()
        )


def seed_master(
    session_factory: sessionmaker[Session],
    *,
    sku_id: str = "SKU-001",
    warehouse_id: str = "WH-01",
) -> None:
    """写入满足事件外键的主数据（SKU + 仓库）。"""
    repo = MasterDataRepository(session_factory)
    repo.add_sku(sku_id=sku_id, name="苏打水 330ml")
    repo.add_warehouse(warehouse_id=warehouse_id, name="一号仓")


def test_master_mixed_quality_rows(
    tmp_path: Path,
    import_manager: CsvImportManager,
    session_factory: sessionmaker[Session],
) -> None:
    """混合质量主数据：1 行入库，3 行进 import_error 且五要素齐全。"""
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
    assert summary.status == IMPORT_STATUS_COMPLETED
    assert (summary.row_count, summary.inserted, summary.skipped, summary.error_count) == (
        4,
        1,
        0,
        3,
    )

    # 合法行入库（错误隔离：非法行不影响合法行）
    repo = MasterDataRepository(session_factory)
    sku = repo.get_sku_by_sku_id("SKU-001")
    assert sku is not None
    assert sku.name == "苏打水 330ml"
    assert sku.category == "饮料"
    assert sku.unit_cost == Decimal("2.50")
    assert repo.get_sku_by_sku_id("SKU-002") is None

    # 非法行五要素齐全（row_no / field_name / error_code / raw_value / suggestion）
    records = import_manager.list_errors(summary.batch_id)
    assert len(records) == 3
    by_row = {record.row_no: record for record in records}
    assert set(by_row) == {3, 4, 5}

    negative = by_row[3]
    assert (negative.field_name, negative.error_code, negative.raw_value) == (
        "unit_cost",
        ERROR_UNIT_COST_INVALID,
        "-1",
    )
    missing = by_row[4]
    assert (missing.field_name, missing.error_code) == ("sku_id", ERROR_REQUIRED_MISSING)
    unparsable = by_row[5]
    assert (unparsable.field_name, unparsable.error_code, unparsable.raw_value) == (
        "unit_cost",
        ERROR_DECIMAL_INVALID,
        "abc",
    )
    for record in records:
        assert record.suggestion  # 中文修复建议不缺


def test_master_duplicate_sku_recorded_as_error(
    tmp_path: Path,
    import_manager: CsvImportManager,
    session_factory: sessionmaker[Session],
) -> None:
    """主数据重复 sku_id：记为错误行（create-only，不覆盖已有数据）。"""
    first_path = write_csv(
        tmp_path / "skus_first.csv",
        [MASTER_HEADER, ["SKU-001", "苏打水 330ml", "饮料", "瓶", "2.50"]],
    )
    import_manager.run_import(
        path=first_path,
        import_type=IMPORT_TYPE_MASTER,
        mapping=identity_mapping(MASTER_HEADER),
    )

    # 内容有差异（新文件新 hash），含一行已存在 SKU + 一行新 SKU
    second_path = write_csv(
        tmp_path / "skus_second.csv",
        [
            MASTER_HEADER,
            ["SKU-001", "苏打水 330ml（旧）", "饮料", "瓶", "9.99"],  # 行 2：已存在
            ["SKU-002", "咖啡 250ml", "饮料", "罐", "3.00"],  # 行 3：新
        ],
    )
    summary = import_manager.run_import(
        path=second_path,
        import_type=IMPORT_TYPE_MASTER,
        mapping=identity_mapping(MASTER_HEADER),
    )

    assert isinstance(summary, ImportRunSummary)
    assert (summary.inserted, summary.error_count) == (1, 1)
    (record,) = import_manager.list_errors(summary.batch_id)
    assert (record.row_no, record.field_name, record.error_code, record.raw_value) == (
        2,
        "sku_id",
        ERROR_SKU_DUPLICATE,
        "SKU-001",
    )

    # 已有数据原样保留（不覆盖）
    sku = MasterDataRepository(session_factory).get_sku_by_sku_id("SKU-001")
    assert sku is not None
    assert sku.name == "苏打水 330ml"
    assert sku.unit_cost == Decimal("2.50")


def test_gbk_encoded_csv(
    tmp_path: Path,
    import_manager: CsvImportManager,
    session_factory: sessionmaker[Session],
) -> None:
    """GBK 编码样本：探测退到 GBK，中文内容往返一致。"""
    csv_path = write_csv(
        tmp_path / "skus_gbk.csv",
        [MASTER_HEADER, ["SKU-001", "苏打水 330ml", "饮料", "瓶", "2.50"]],
        encoding="gbk",
    )

    assert detect_encoding(csv_path) == ENCODING_GBK
    snapshot = import_manager.read_csv(csv_path)
    assert snapshot.encoding == ENCODING_GBK
    assert snapshot.rows[0]["name"] == "苏打水 330ml"

    summary = import_manager.run_import(
        path=csv_path,
        import_type=IMPORT_TYPE_MASTER,
        mapping=identity_mapping(MASTER_HEADER),
    )
    assert isinstance(summary, ImportRunSummary)
    assert summary.inserted == 1
    sku = MasterDataRepository(session_factory).get_sku_by_sku_id("SKU-001")
    assert sku is not None
    assert sku.name == "苏打水 330ml"
    assert sku.category == "饮料"


def test_event_import_error_isolation(
    tmp_path: Path,
    import_manager: CsvImportManager,
    session_factory: sessionmaker[Session],
) -> None:
    """事件导入错误隔离：SKU 不存在/类型非法/数量非正各自记错，合法行入库。"""
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
    assert summary.status == IMPORT_STATUS_COMPLETED
    assert (summary.row_count, summary.inserted, summary.error_count) == (5, 1, 4)

    from local_data.repository import InventoryEventRepository

    event = InventoryEventRepository(session_factory).get_event("E-001")
    assert event is not None
    assert event.move_type == "INBOUND"
    assert event.quantity == Decimal(10)
    assert event.occurred_at == "2026-08-01T00:00:00+00:00"  # YYYY-MM-DD → UTC ISO
    assert event.source == "IMPORT_CSV"

    records = import_manager.list_errors(summary.batch_id)
    codes = {(record.row_no, record.error_code) for record in records}
    assert codes == {
        (3, ERROR_SKU_NOT_FOUND),
        (4, ERROR_MOVE_TYPE_INVALID),
        (5, ERROR_QUANTITY_INVALID),
        (6, "WAREHOUSE_NOT_FOUND"),
    }
    sku_error = next(r for r in records if r.error_code == ERROR_SKU_NOT_FOUND)
    assert (sku_error.field_name, sku_error.raw_value) == ("sku_id", "SKU-404")
    assert "主数据" in (sku_error.suggestion or "")


def test_event_import_rebuilds_projection(
    tmp_path: Path,
    import_manager: CsvImportManager,
    session_factory: sessionmaker[Session],
) -> None:
    """事件导入后余额投影已更新：按 occurred_at 回放结果正确。"""
    seed_master(session_factory)
    csv_path = write_csv(
        tmp_path / "events.csv",
        [
            EVENT_HEADER,
            ["E-IN-1", "SKU-001", "WH-01", "INBOUND", "100", "2026-08-01"],
            ["E-OUT-1", "SKU-001", "WH-01", "OUTBOUND", "30", "2026-08-02"],
            ["E-RET-1", "SKU-001", "WH-01", "RETURN", "5", "2026-08-03"],
        ],
    )
    summary = import_manager.run_import(
        path=csv_path,
        import_type=IMPORT_TYPE_EVENTS,
        mapping=identity_mapping(EVENT_HEADER),
    )
    assert isinstance(summary, ImportRunSummary)
    assert summary.inserted == 3

    balances = BalanceProjectionService(session_factory).list_balances()
    assert len(balances) == 1
    (balance,) = balances
    assert balance.on_hand_qty == Decimal(75)  # 100 - 30 + 5
    assert balance.available_qty == Decimal(75)
    assert balance.as_of_event_id == "E-RET-1"


def test_event_idempotent_reimport_skips(
    tmp_path: Path,
    import_manager: CsvImportManager,
    session_factory: sessionmaker[Session],
) -> None:
    """不同文件（新 hash）携带已导入 event_id：幂等跳过并入批次统计。"""
    seed_master(session_factory)
    first_path = write_csv(
        tmp_path / "events_first.csv",
        [
            EVENT_HEADER,
            ["E-001", "SKU-001", "WH-01", "INBOUND", "10", "2026-08-01"],
            ["E-002", "SKU-001", "WH-01", "OUTBOUND", "4", "2026-08-02"],
        ],
    )
    import_manager.run_import(
        path=first_path,
        import_type=IMPORT_TYPE_EVENTS,
        mapping=identity_mapping(EVENT_HEADER),
    )

    second_path = write_csv(
        tmp_path / "events_second.csv",
        [
            EVENT_HEADER,
            ["E-001", "SKU-001", "WH-01", "INBOUND", "10", "2026-08-01"],  # 已导入
            ["E-002", "SKU-001", "WH-01", "OUTBOUND", "4", "2026-08-02"],  # 已导入
            ["E-003", "SKU-001", "WH-01", "INBOUND", "6", "2026-08-03"],  # 新
        ],
    )
    summary = import_manager.run_import(
        path=second_path,
        import_type=IMPORT_TYPE_EVENTS,
        mapping=identity_mapping(EVENT_HEADER),
    )

    assert isinstance(summary, ImportRunSummary)
    assert (summary.inserted, summary.skipped, summary.error_count) == (1, 2, 0)
    # 幂等：事件总数 3（无重复入库），投影仍正确（10 - 4 + 6 = 12）
    balances = BalanceProjectionService(session_factory).list_balances()
    (balance,) = balances
    assert balance.on_hand_qty == Decimal(12)


def test_duplicate_file_hash_blocked(
    tmp_path: Path,
    import_manager: CsvImportManager,
    session_factory: sessionmaker[Session],
) -> None:
    """重复 file_hash：返回 DuplicateImportFound 指向历史批次，不新增批次。"""
    csv_path = write_csv(
        tmp_path / "skus.csv",
        [MASTER_HEADER, ["SKU-001", "苏打水 330ml", "饮料", "瓶", "2.50"]],
    )
    mapping = identity_mapping(MASTER_HEADER)
    first = import_manager.run_import(
        path=csv_path, import_type=IMPORT_TYPE_MASTER, mapping=mapping
    )
    assert isinstance(first, ImportRunSummary)
    assert batch_count(session_factory) == 1

    second = import_manager.run_import(
        path=csv_path, import_type=IMPORT_TYPE_MASTER, mapping=mapping
    )

    assert isinstance(second, DuplicateImportFound)
    assert second.batch_id == first.batch_id
    assert second.file_hash  # SHA-256 十六进制
    assert batch_count(session_factory) == 1  # 未创建新批次（不静默重复）
    assert import_manager.list_errors(first.batch_id) == []


def test_missing_required_column_blocked(
    tmp_path: Path,
    import_manager: CsvImportManager,
    session_factory: sessionmaker[Session],
) -> None:
    """缺必填映射：CsvImportError 阻断，不创建批次。"""
    header = ["sku_id", "category", "unit", "unit_cost"]  # 缺 name
    csv_path = write_csv(tmp_path / "skus_no_name.csv", [header, ["SKU-001", "饮料", "瓶", "2.5"]])
    mapping = identity_mapping(header)

    with pytest.raises(CsvImportError) as excinfo:
        import_manager.run_import(
            path=csv_path, import_type=IMPORT_TYPE_MASTER, mapping=mapping
        )
    assert excinfo.value.error_code == ERROR_MISSING_COLUMN
    assert "name" in excinfo.value.message
    assert batch_count(session_factory) == 0  # 阻断在批次创建之前
    assert MasterDataRepository(session_factory).get_sku_by_sku_id("SKU-001") is None


def test_all_rows_failed_marks_batch_failed(
    tmp_path: Path,
    import_manager: CsvImportManager,
    session_factory: sessionmaker[Session],
) -> None:
    """全部行失败：批次置 FAILED 且 row_count / error_count 已登记。"""
    from local_data.repository import ImportBatchRepository

    csv_path = write_csv(
        tmp_path / "skus_all_bad.csv",
        [MASTER_HEADER, ["", "无名", "饮料", "瓶", "1"], ["", "无名 2", "饮料", "瓶", "1"]],
    )
    summary = import_manager.run_import(
        path=csv_path,
        import_type=IMPORT_TYPE_MASTER,
        mapping=identity_mapping(MASTER_HEADER),
    )

    assert isinstance(summary, ImportRunSummary)
    assert summary.status == IMPORT_STATUS_FAILED
    assert (summary.row_count, summary.inserted, summary.error_count) == (2, 0, 2)

    batch = ImportBatchRepository(session_factory).get_batch(summary.batch_id)
    assert batch is not None
    assert batch.status == IMPORT_STATUS_FAILED
    assert (batch.row_count, batch.error_count) == (2, 2)  # 计数照常登记


def test_encoding_detection_failure(tmp_path: Path) -> None:
    """UTF-8 与 GBK 均无法解码：以 IMPORT_ENCODING_FAILED 阻断。"""
    bad_path = tmp_path / "bad.csv"
    bad_path.write_bytes(b"\xff\xff\xff\xff")  # 0xFF 非法于两种编码

    with pytest.raises(CsvImportError) as excinfo:
        detect_encoding(bad_path)
    assert excinfo.value.error_code == ERROR_ENCODING
