"""0006_report_backup 迁移与仓储测试（M1 Task 8/9）。

- 迁移结构：report_artifact / backup_record 建表、UNIQUE/FK/CHECK 实际生效；
- ReportArtifactRepository：(run_id, format) 幂等 upsert（重复导出更新不报错）；
- BackupRecordRepository：CREATED → VERIFIED / FAILED 状态流转与查询；
- AnalysisRepository.get_run：按 run_id 取运行元数据。
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest
from alembic.config import Config
from contracts import AnalysisResult, InputSummary, ResultMetric
from local_data.models import (
    BACKUP_STATUS_VERIFIED,
    REPORT_FORMAT_CSV,
    REPORT_FORMAT_HTML,
)
from local_data.repository import (
    AnalysisRepository,
    BackupRecordRepository,
    ReportArtifactRepository,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker


def _make_result(run_id: str = "run-report-0001") -> AnalysisResult:
    """构造最小可用 AnalysisResult（满足 contracts 必填字段）。"""
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
        summary="报告备份测试",
        input_summary=InputSummary(
            sku_count=1,
            movement_count=1,
            snapshot_count=0,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            dataset_digest="0" * 64,
        ),
    )


def _insert_run_raw(session_factory: sessionmaker[Session], run_id: str) -> None:
    """绕过仓储直接插入 analysis_run（构造 FK 场景用）。"""
    with session_factory() as session, session.begin():
        session.execute(
            text(
                "INSERT INTO analysis_run"
                " (run_id, engine_version, formula_version, status, created_at, updated_at)"
                " VALUES (:rid, '0.1.0', '0.1.0', 'SUCCEEDED',"
                " '2026-08-29T00:00:00+00:00', '2026-08-29T00:00:00+00:00')"
            ),
            {"rid": run_id},
        )


# ---------------------------------------------------------------------------
# 迁移结构
# ---------------------------------------------------------------------------


def test_report_backup_schema_constraints(alembic_cfg: Config, data_dir: Path) -> None:
    """0006 两表存在，UNIQUE (run_id, format)、FK、枚举 CHECK 实际生效。"""
    from alembic import command
    from local_data.connection import connect

    command.upgrade(alembic_cfg, "head")
    engine, _session_factory = connect(data_dir)
    try:
        with engine.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
            }
            assert {"report_artifact", "backup_record"} <= tables

            ddl_artifact = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE name='report_artifact'")
            ).scalar_one()
            assert "CONSTRAINT uq_report_artifact_run_id_format UNIQUE (run_id, format)" in ddl_artifact

            ddl_backup = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE name='backup_record'")
            ).scalar_one()
            assert "backup_type IN ('MANUAL', 'AUTO')" in ddl_backup
            assert "status IN ('CREATED', 'VERIFIED', 'FAILED')" in ddl_backup

        # 枚举 CHECK 实际生效：非法 backup_type / status 被拒
        with engine.connect() as conn:
            try:
                conn.execute(
                    text(
                        "INSERT INTO backup_record"
                        " (backup_id, file_path, backup_type, db_schema_version,"
                        " sha256, size_bytes, status, created_at, updated_at)"
                        " VALUES ('bak-bad', 'x.db', 'WEEKLY', 'local-0006',"
                        " '00', 1, 'CREATED', '2026-08-29T00:00:00+00:00',"
                        " '2026-08-29T00:00:00+00:00')"
                    )
                )
                conn.commit()
                bad_type_rejected = False
            except IntegrityError:
                bad_type_rejected = True
            assert bad_type_rejected

            try:
                conn.execute(
                    text(
                        "INSERT INTO backup_record"
                        " (backup_id, file_path, backup_type, db_schema_version,"
                        " sha256, size_bytes, status, created_at, updated_at)"
                        " VALUES ('bak-bad2', 'x.db', 'MANUAL', 'local-0006',"
                        " '00', 1, 'PENDING', '2026-08-29T00:00:00+00:00',"
                        " '2026-08-29T00:00:00+00:00')"
                    )
                )
                conn.commit()
                bad_status_rejected = False
            except IntegrityError:
                bad_status_rejected = True
            assert bad_status_rejected

        # FK 实际生效：report_artifact 挂孤儿 run_id 被拒
        with engine.connect() as conn:
            try:
                conn.execute(
                    text(
                        "INSERT INTO report_artifact"
                        " (report_id, run_id, format, file_path, sha256,"
                        " created_at, updated_at)"
                        " VALUES ('r1', 'run-not-exist', 'CSV', 'x.csv', '00',"
                        " '2026-08-29T00:00:00+00:00', '2026-08-29T00:00:00+00:00')"
                    )
                )
                conn.commit()
                orphan_rejected = False
            except IntegrityError:
                orphan_rejected = True
            assert orphan_rejected
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# ReportArtifactRepository：幂等 upsert
# ---------------------------------------------------------------------------


def test_report_artifact_upsert_round_trip(session_factory: sessionmaker[Session]) -> None:
    """upsert 首次插入；同 (run_id, format) 再次 upsert 更新记录不报错。"""
    runs = AnalysisRepository(session_factory)
    runs.save_result(_make_result("run-report-0001"))
    artifacts = ReportArtifactRepository(session_factory)

    first = artifacts.upsert(
        report_id="report-aaa",
        run_id="run-report-0001",
        format=REPORT_FORMAT_CSV,
        file_path=r"C:\tmp\run-report-0001.csv",
        sha256="a" * 64,
    )
    assert first.format == REPORT_FORMAT_CSV

    second = artifacts.upsert(
        report_id="report-bbb",
        run_id="run-report-0001",
        format=REPORT_FORMAT_CSV,
        file_path=r"C:\tmp\run-report-0001.csv",
        sha256="b" * 64,
    )
    assert second.sha256 == "b" * 64
    loaded = artifacts.get("run-report-0001", REPORT_FORMAT_CSV)
    assert loaded is not None
    assert loaded.report_id == "report-bbb"  # 更新为最新一次导出
    assert loaded.sha256 == "b" * 64
    assert len(artifacts.list_for_run("run-report-0001")) == 1  # 始终一条记录

    # 不同 format 是另一条记录
    artifacts.upsert(
        report_id="report-ccc",
        run_id="run-report-0001",
        format=REPORT_FORMAT_HTML,
        file_path=r"C:\tmp\run-report-0001.html",
        sha256="c" * 64,
    )
    assert len(artifacts.list_for_run("run-report-0001")) == 2


def test_report_artifact_unique_enforced(session_factory: sessionmaker[Session]) -> None:
    """(run_id, format) UNIQUE 数据库约束实际生效（直接插入重复行被拒）。"""
    _insert_run_raw(session_factory, "run-raw-0001")
    with session_factory() as session, session.begin():
        session.execute(
            text(
                "INSERT INTO report_artifact"
                " (report_id, run_id, format, file_path, sha256, created_at, updated_at)"
                " VALUES ('r1', 'run-raw-0001', 'CSV', 'a.csv', '11',"
                " '2026-08-29T00:00:00+00:00', '2026-08-29T00:00:00+00:00')"
            )
        )
    # SQLite 立即执行 INSERT：约束冲突在 execute 时抛出（非 flush 时）
    with session_factory() as session, session.begin(), pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO report_artifact"
                " (report_id, run_id, format, file_path, sha256, created_at, updated_at)"
                " VALUES ('r2', 'run-raw-0001', 'CSV', 'a.csv', '22',"
                " '2026-08-29T00:00:00+00:00', '2026-08-29T00:00:00+00:00')"
            )
        )


# ---------------------------------------------------------------------------
# BackupRecordRepository：状态流转
# ---------------------------------------------------------------------------


def test_backup_record_status_flow(session_factory: sessionmaker[Session]) -> None:
    """create（CREATED）→ mark_verified（VERIFIED + verified_at）→ 查询。"""
    records = BackupRecordRepository(session_factory)
    backup_id = f"bak-{uuid.uuid4().hex[:12]}"
    created = records.create(
        backup_id=backup_id,
        file_path=r"C:\tmp\backup-1.db",
        backup_type="MANUAL",
        db_schema_version="local-0006",
        sha256="d" * 64,
        size_bytes=4096,
    )
    assert created.status == "CREATED"
    assert created.verified_at is None

    verified = records.mark_verified(backup_id)
    assert verified is not None
    assert verified.status == BACKUP_STATUS_VERIFIED
    assert verified.verified_at is not None

    loaded = records.get_by_backup_id(backup_id)
    assert loaded is not None and loaded.status == BACKUP_STATUS_VERIFIED

    failed = records.mark_failed(backup_id)
    assert failed is not None and failed.status == "FAILED"

    # 按 file_path 定位（恢复时校验值比对入口）
    by_path = records.get_by_file_path(r"C:\tmp\backup-1.db")
    assert by_path is not None and by_path.backup_id == backup_id

    # 列表新 → 旧
    records.create(
        backup_id=f"bak-{uuid.uuid4().hex[:12]}",
        file_path=r"C:\tmp\backup-2.db",
        backup_type="AUTO",
        db_schema_version="local-0006",
        sha256="e" * 64,
        size_bytes=8192,
    )
    assert len(records.list_records()) == 2


# ---------------------------------------------------------------------------
# AnalysisRepository.get_run
# ---------------------------------------------------------------------------


def test_get_run_metadata(session_factory: sessionmaker[Session]) -> None:
    """get_run 返回期间与版本元数据；不存在返回 None。"""
    runs = AnalysisRepository(session_factory)
    runs.save_result(_make_result("run-report-0002"), task_id="task-002")
    meta = runs.get_run("run-report-0002")
    assert meta is not None
    assert meta["start_date"] == "2026-08-01"
    assert meta["end_date"] == "2026-08-31"
    assert meta["engine_version"] == "0.1.0-fake"
    assert meta["formula_version"] == "0.1.0"
    assert runs.get_run("run-not-exist") is None
