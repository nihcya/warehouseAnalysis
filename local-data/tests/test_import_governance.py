"""Task 4 测试：导入治理迁移（0005_import）与批次/错误行仓储。

覆盖：空库升级/回滚、批次状态流转 RUNNING→COMPLETED/FAILED、
错误行按 (batch_id, row_no) 定位、raw_value/suggestion 完整存取、
重复 file_hash 检出。
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from local_data.connection import connect
from local_data.models import IMPORT_STATUS_COMPLETED, IMPORT_STATUS_RUNNING
from local_data.repository import ImportBatchRepository, ImportErrorRepository
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

IMPORT_TABLES = {"import_batch", "import_error"}


def _table_names(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).all()
    return {name for (name,) in rows}


def _schema_version(engine) -> str:
    with engine.connect() as conn:
        meta = dict(conn.execute(text("SELECT key, value FROM local_meta")).all())
    return meta["db_schema_version"]


def test_upgrade_0005_import_then_downgrade(alembic_cfg: Config, data_dir: Path) -> None:
    """空库升级到 0005：2 表齐、索引在、版本 local-0005；-1 回滚后表全消失。"""
    engine, _factory = connect(data_dir)
    try:
        command.upgrade(alembic_cfg, "0005_import")
        assert IMPORT_TABLES <= _table_names(engine)
        assert _schema_version(engine) == "local-0005"
        with engine.connect() as conn:
            indexes = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='index'")
                ).all()
            }
        assert {"ix_import_batch_file_hash", "ix_import_error_batch_id_row_no"} <= indexes

        command.downgrade(alembic_cfg, "-1")
        assert not IMPORT_TABLES & _table_names(engine)
        assert _schema_version(engine) == "local-0004"
    finally:
        engine.dispose()


def test_batch_lifecycle_running_to_completed(
    session_factory: sessionmaker[Session],
) -> None:
    """批次状态流转：start RUNNING（计数 0）→ complete COMPLETED（登记行数/错误数）。"""
    batch_repo = ImportBatchRepository(session_factory)
    started = batch_repo.start_batch(
        batch_id="B-20260829-001",
        file_name="events_202608.csv",
        file_hash="a" * 64,
        source_type="CSV",
    )
    assert started.status == IMPORT_STATUS_RUNNING
    assert started.row_count == 0
    assert started.error_count == 0
    assert started.started_at is not None
    assert started.completed_at is None

    error_repo = ImportErrorRepository(session_factory)
    error_repo.add_error(
        batch_id="B-20260829-001",
        row_no=7,
        field_name="quantity",
        error_code="NONPOSITIVE_QUANTITY",
        raw_value="-5",
        suggestion="数量必须大于 0",
    )

    completed = batch_repo.complete_batch(
        "B-20260829-001", row_count=100, error_count=1
    )
    assert completed is not None
    assert completed.status == IMPORT_STATUS_COMPLETED
    assert completed.row_count == 100
    assert completed.error_count == 1
    assert completed.completed_at is not None

    # 重复导入提示：同 file_hash 的已完成批次可检出
    found = batch_repo.find_completed_by_file_hash("a" * 64)
    assert found is not None
    assert found.batch_id == "B-20260829-001"


def test_batch_lifecycle_running_to_failed(
    session_factory: sessionmaker[Session],
) -> None:
    """批次失败流转：start → fail 置 FAILED 并记录结束时间；未知批次返回 None。"""
    batch_repo = ImportBatchRepository(session_factory)
    batch_repo.start_batch(
        batch_id="B-FAIL-001",
        file_name="broken.csv",
        file_hash="b" * 64,
        source_type="CSV",
    )
    failed = batch_repo.fail_batch("B-FAIL-001")
    assert failed is not None
    assert failed.status == "FAILED"
    assert failed.completed_at is not None

    assert batch_repo.complete_batch("B-NONE", row_count=1, error_count=0) is None
    assert batch_repo.fail_batch("B-NONE") is None
    assert batch_repo.get_batch("B-NONE") is None
    # RUNNING 批次不算已完成，重复 hash 检出只针对 COMPLETED
    assert batch_repo.find_completed_by_file_hash("b" * 64) is None


def test_error_rows_located_by_batch_and_row(
    session_factory: sessionmaker[Session],
) -> None:
    """错误行按 (batch_id, row_no) 查询定位；raw_value/suggestion 完整存取。"""
    batch_repo = ImportBatchRepository(session_factory)
    error_repo = ImportErrorRepository(session_factory)
    batch_repo.start_batch(
        batch_id="B-ERR-001",
        file_name="mixed.csv",
        file_hash="c" * 64,
        source_type="CSV",
    )

    error_repo.add_error(
        batch_id="B-ERR-001",
        row_no=2,
        field_name="sku_id",
        error_code="SKU_NOT_FOUND",
        raw_value="SKU-404",
        suggestion="先在主数据中导入该 SKU",
    )
    error_repo.add_error(
        batch_id="B-ERR-001",
        row_no=7,
        field_name="quantity",
        error_code="NONPOSITIVE_QUANTITY",
        raw_value="-5",
        suggestion="数量必须大于 0",
    )
    # 同一行允许报多个字段错误（错误隔离到字段级）
    error_repo.add_error(
        batch_id="B-ERR-001",
        row_no=7,
        field_name="occurred_at",
        error_code="BAD_DATE_FORMAT",
        raw_value="2026/8/29",
        suggestion="日期须为 YYYY-MM-DD",
    )

    listed = error_repo.list_errors("B-ERR-001")
    assert [error.row_no for error in listed] == [2, 7, 7]  # 按行号稳定排序

    row7 = error_repo.errors_for_row("B-ERR-001", 7)
    assert len(row7) == 2
    by_field = {error.field_name: error for error in row7}
    qty_error = by_field["quantity"]
    assert qty_error.error_code == "NONPOSITIVE_QUANTITY"
    assert qty_error.raw_value == "-5"  # 原始值原样保留
    assert qty_error.suggestion == "数量必须大于 0"  # 修复建议完整存取（中文）
    date_error = by_field["occurred_at"]
    assert date_error.error_code == "BAD_DATE_FORMAT"
    assert date_error.resolved_at is None  # 未处置

    assert error_repo.errors_for_row("B-ERR-001", 3) == []  # 合法行无错误记录
    assert error_repo.list_errors("B-OTHER") == []  # 其他批次隔离
