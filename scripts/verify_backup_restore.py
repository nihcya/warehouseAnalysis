"""verify_backup_restore.py：备份与恢复演练（spec M1 Task 9.4，§35.9）。

CLI 全流程演练（模仿 verify_schema.py 风格，全程 tmp 目录，不触碰真实数据目录）：

1. 建临时库 → ``alembic upgrade head``（迁移到 0006_report_backup）；
2. 造数据（分析结果 + 主数据 + 库存事件）；
3. 手动备份 → 校验（backup_record.status=VERIFIED、SHA-256 匹配、
   verified_at 非空、db_schema_version 记录、关键表行数）；
4. 篡改当前库（新增数据）后恢复 → 校验（行数守恒回到备份时点、
   库可正常打开查询）；
5. 损坏备份文件 → 恢复必须失败且当前库字节零改动（恢复失败保护）。

运行（仓库根）::

    uv run python scripts/verify_backup_restore.py

退出码：0 = 全部通过；1 = 存在失败项。

说明：workbench-desktop 为应用型 workspace 成员（不安装为可分发包），
脚本通过 ``sys.path`` 注入其包根后导入 ``app.infrastructure``。
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DATA_ROOT = REPO_ROOT / "local-data"
WORKBENCH_ROOT = REPO_ROOT / "apps" / "workbench-desktop"

if str(WORKBENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKBENCH_ROOT))

from alembic import command
from alembic.config import Config
from app.infrastructure.backup.backup_service import KEY_TABLES, BackupService
from contracts import AnalysisResult, InputSummary, ResultMetric, Warning
from local_data.connection import DB_FILE_NAME, connect, database_url
from local_data.repository import (
    AnalysisRepository,
    InventoryEventCreate,
    InventoryEventRepository,
    MasterDataRepository,
)

PASSED: list[str] = []
FAILED: list[str] = []


def check(ok: bool, message: str) -> None:
    """记录并打印一项检查结果。"""
    if ok:
        PASSED.append(message)
        print(f"  [ok]   {message}")
    else:
        FAILED.append(message)
        print(f"  [FAIL] {message}")


def _sha256_file(path: Path) -> str:
    """计算文件 SHA-256（当前库零改动断言用）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_counts(db_path: Path) -> dict[str, int]:
    """独立 sqlite3 只读连接读关键表行数（不依赖 SQLAlchemy 会话状态）。"""
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as conn:
        return {
            table: int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in KEY_TABLES
        }


def _make_result(run_id: str) -> AnalysisResult:
    """构造演练用 AnalysisResult（满足 contracts 必填字段）。"""
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
        warnings=[
            Warning(
                code="ANALYSIS_PLACEHOLDER",
                severity="INFO",
                message="演练占位结果",
                fields=[],
                blocking=False,
            )
        ],
        summary="备份恢复演练",
        input_summary=InputSummary(
            sku_count=2,
            movement_count=3,
            snapshot_count=0,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            dataset_digest="0" * 64,
        ),
    )


def main() -> int:
    print("verify_backup_restore.py：备份与恢复演练（VACUUM INTO 全量备份 + 安全恢复）")
    with tempfile.TemporaryDirectory(prefix="verify-backup-restore-") as tmp:
        data_dir = Path(tmp) / "wb-data"
        backup_dir = Path(tmp) / "backups"

        print("\n[step 1] 建临时库并 alembic upgrade head")
        cfg = Config()
        cfg.set_main_option("script_location", str(LOCAL_DATA_ROOT / "alembic"))
        cfg.set_main_option("sqlalchemy.url", database_url(data_dir))
        command.upgrade(cfg, "head")
        db_path = data_dir / DB_FILE_NAME
        check(db_path.exists(), "临时库已创建并迁移到 head（0006_report_backup）")

        engine, session_factory = connect(data_dir)
        try:
            print("\n[step 2] 造数据（分析结果 + 主数据 + 库存事件）")
            analysis = AnalysisRepository(session_factory)
            master = MasterDataRepository(session_factory)
            events = InventoryEventRepository(session_factory)
            analysis.save_result(_make_result("run-verify-0001"), task_id="task-verify")
            master.add_warehouse(warehouse_id="WH-01", name="主仓")
            master.add_sku(sku_id="SKU-001", name="矿泉水 550ml", category="饮料", unit="瓶")
            master.add_sku(sku_id="SKU-002", name="苏打水 330ml", category="饮料", unit="罐")
            events.upsert_events(
                [
                    InventoryEventCreate(
                        event_id="EVT-001",
                        sku_id="SKU-001",
                        warehouse_id="WH-01",
                        move_type="INBOUND",
                        quantity=Decimal(100),
                        occurred_at="2026-08-01T08:00:00+00:00",
                        source="IMPORT_CSV",
                    ),
                    InventoryEventCreate(
                        event_id="EVT-002",
                        sku_id="SKU-002",
                        warehouse_id="WH-01",
                        move_type="INBOUND",
                        quantity=Decimal(40),
                        occurred_at="2026-08-02T08:00:00+00:00",
                        source="IMPORT_CSV",
                    ),
                    InventoryEventCreate(
                        event_id="EVT-003",
                        sku_id="SKU-001",
                        warehouse_id="WH-01",
                        move_type="OUTBOUND",
                        quantity=Decimal(30),
                        occurred_at="2026-08-05T08:00:00+00:00",
                        source="IMPORT_CSV",
                    ),
                ]
            )
            counts_before = _row_counts(db_path)
            check(
                counts_before["sku"] == 2 and counts_before["inventory_event"] == 3,
                f"造数据落库（sku={counts_before['sku']}"
                f"、inventory_event={counts_before['inventory_event']}）",
            )

            print("\n[step 3] 手动备份 → 校验（VERIFIED / SHA-256 / 行数）")
            service = BackupService(session_factory)
            backup = service.backup(db_path, backup_dir)
            record = service.get_backup(backup.backup_id)
            check(backup.status == "VERIFIED", f"backup_record.status = {backup.status}")
            check(backup.file_path.exists(), f"备份文件已生成：{backup.file_path.name}")
            check(
                backup.sha256 == _sha256_file(backup.file_path),
                "备份文件 SHA-256 与登记值一致",
            )
            check(
                record is not None and record.verified_at is not None,
                "VERIFIED 备份记录了验证时间（verified_at）",
            )
            check(
                record is not None and record.db_schema_version == "local-0006",
                "db_schema_version 从 local_meta 读取（local-0006）",
            )
            check(
                backup.row_counts.get("analysis_run") == 1,
                f"备份关键表行数可读（analysis_run={backup.row_counts.get('analysis_run')}）",
            )

            print("\n[step 4] 篡改当前库（新增一个 run）后恢复 → 行数守恒校验")
            analysis.save_result(_make_result("run-verify-0002"))
            counts_tampered = _row_counts(db_path)
            check(counts_tampered["analysis_run"] == 2, "恢复前当前库已变化（2 个 run）")
            restored = service.restore(
                backup.file_path, db_path, safety_backup_dir=backup_dir
            )
            check(restored.success, "恢复流程成功（先校验后原子替换）")
            counts_restored = _row_counts(db_path)
            check(
                counts_restored == counts_before,
                f"恢复后关键表行数守恒（analysis_run={counts_restored['analysis_run']}，"
                "与备份时一致）",
            )
            check(
                restored.safety_backup is not None
                and restored.safety_backup.status == "VERIFIED",
                "恢复前已自动安全备份当前库（AUTO 类型，§35.9）",
            )
            safety_record = (
                service.get_backup(restored.safety_backup.backup_id)
                if restored.safety_backup is not None
                else None
            )
            check(
                safety_record is not None and safety_record.backup_type == "AUTO",
                "安全备份记录已补登记进恢复后的库（非孤儿文件，可再次恢复）",
            )
            with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as conn:
                version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            check(str(version) == "0006_report_backup", "恢复后 alembic 版本保持 head")
            loaded = analysis.get_result("run-verify-0001")
            check(
                loaded is not None and loaded.run_id == "run-verify-0001",
                "恢复后业务数据可正常读取（run-verify-0001）",
            )

            print("\n[step 5] 损坏备份文件 → 恢复失败且当前库零改动")
            before_hash = _sha256_file(db_path)
            corrupted = backup.file_path.with_name(backup.file_path.name + ".corrupted")
            # 截断一半（页缺失必然触发 integrity_check 失败），模拟损坏/篡改
            data = backup.file_path.read_bytes()
            corrupted.write_bytes(data[: len(data) // 2])
            failed = service.restore(corrupted, db_path)
            check(not failed.success, "损坏备份的恢复被拒绝（失败返回而非崩溃）")
            check(
                failed.error is not None and "当前库未做任何改动" in failed.error,
                "失败信息明确说明当前库未改动",
            )
            check(
                _sha256_file(db_path) == before_hash,
                "当前库文件字节零改动（前后 SHA-256 一致）",
            )
        finally:
            engine.dispose()

    print("\n===== 结果 =====")
    print(f"通过 {len(PASSED)} 项；失败 {len(FAILED)} 项")
    if FAILED:
        print("失败项：")
        for item in FAILED:
            print(f"  - {item}")
        return 1
    print("全部通过：备份可验证、恢复守恒、失败零改动")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
