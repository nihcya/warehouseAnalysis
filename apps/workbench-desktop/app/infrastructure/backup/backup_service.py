"""备份与恢复服务（M1 Task 9，§35.9 备份/恢复原则）：纯逻辑，不依赖 Qt。

安全设计（核心不变量：备份/恢复操作绝不能损坏当前库）：

- backup：SQLite ``VACUUM INTO`` 生成全量一致备份（含 WAL 已提交内容），
  计算 SHA-256 后登记 ``backup_record``（先 CREATED），再以独立只读连接
  读回复验（``PRAGMA integrity_check`` + 关键表行数可读）→ VERIFIED /
  失败 → FAILED；
- restore：先校验后替换，任何一步失败当前库零字节改动——
  1. 校验值比对：按备份文件路径找 ``backup_record``，sha256 不一致
     （文件损坏/被篡改）立即失败；
  2. 复制到临时路径（与当前库同目录，保证 ``os.replace`` 原子且不跨卷），
     连接校验：integrity_check、alembic 版本与当前库一致、关键表行数可读；
  3. 校验通过后（可选）先把当前库安全备份一份（AUTO 类型，§35.9
     “恢复前自动备份当前库”），再 checkpoint 当前库 WAL、清理
     ``-wal``/``-shm``、``os.replace`` 原子替换；
  4. 失败路径不写任何 ``backup_record``（避免改动当前库字节），
     以返回结构 ``RestoreResult`` 上报中文错误信息。

- ``db_schema_version`` 从 ``local_meta``（key=db_schema_version）读取，
  缺失时回退 ``alembic_version`` 表。

文件锁说明（Windows）：替换前必须关闭所有 SQLAlchemy 连接池连接，
本服务在替换前对注入 ``session_factory`` 绑定的 engine 执行 ``dispose()``，
调用方在恢复后需重建会话（UI 提示重启生效）。所有 sqlite3 连接显式关闭，
不留打开句柄（Windows 上打开句柄会锁文件、阻塞替换）。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from local_data.models import (
    BACKUP_STATUS_FAILED,
    BACKUP_STATUS_VERIFIED,
    BACKUP_TYPE_AUTO,
    BACKUP_TYPE_MANUAL,
    BackupRecordRow,
    utc_now_iso,
)
from local_data.repository import BackupRecordRepository
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

#: 备份关键表（复验与恢复校验的行数读取范围；业务核心事实与治理表）
KEY_TABLES: tuple[str, ...] = (
    "sku",
    "inventory_event",
    "inventory_balance",
    "import_batch",
    "analysis_run",
    "analysis_result",
)

#: 备份/临时文件名前缀（VACUUM INTO 要求目标文件事先不存在）
_BACKUP_FILE_PREFIX = "backup"
_RESTORE_TMP_PREFIX = ".restore-tmp"


def _sha256_file(path: Path) -> str:
    """计算文件 SHA-256（流式读取，备份文件可能较大）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quote_sqlite_path(path: Path) -> str:
    """路径转 SQLite 字符串字面量（单引号转义，VACUUM INTO 不支持参数绑定）。"""
    return str(path).replace("'", "''")


def _open_ro(path: Path) -> sqlite3.Connection:
    """以只读模式打开 SQLite 文件（校验阶段绝不写被检文件）。"""
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _read_schema_version(conn: sqlite3.Connection) -> str:
    """读 local_meta.db_schema_version，缺失时回退 alembic_version。"""
    try:
        row = conn.execute(
            "SELECT value FROM local_meta WHERE key = 'db_schema_version'"
        ).fetchone()
        if row is not None:
            return str(row[0])
    except sqlite3.DatabaseError:
        pass
    version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    return str(version[0]) if version is not None else "unknown"


def _read_alembic_version(conn: sqlite3.Connection) -> str | None:
    """读 alembic_version；表不存在（未迁移库）返回 None。"""
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.DatabaseError:
        return None
    return str(row[0]) if row is not None else None


def _table_row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """读关键表行数（表缺失记 -1，交由调用方校验把关）。"""
    counts: dict[str, int] = {}
    for table in KEY_TABLES:
        try:
            counts[table] = int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        except sqlite3.DatabaseError:
            counts[table] = -1
    return counts


def _integrity_ok(conn: sqlite3.Connection) -> bool:
    """PRAGMA integrity_check 结果为 ok。"""
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return row is not None and str(row[0]).lower() == "ok"


@dataclass(frozen=True)
class BackupResult:
    """一次备份的结果（status: VERIFIED / FAILED）。"""

    backup_id: str
    file_path: Path
    sha256: str
    size_bytes: int
    status: str
    db_schema_version: str
    row_counts: dict[str, int] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class RestoreResult:
    """一次恢复的结果；失败时 error 为中文信息且当前库零改动。"""

    backup_path: Path
    success: bool
    error: str | None = None
    row_counts: dict[str, int] = field(default_factory=dict)
    safety_backup: BackupResult | None = None


class BackupService:
    """本地 SQLite 全量备份与安全恢复（组合根注入会话工厂）。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._records = BackupRecordRepository(session_factory)

    # ------------------------------------------------------------------
    # 备份（VACUUM INTO + 读回复验）
    # ------------------------------------------------------------------

    def backup(
        self,
        db_path: Path,
        dest_dir: Path,
        backup_type: str = BACKUP_TYPE_MANUAL,
    ) -> BackupResult:
        """全量备份当前库到 ``dest_dir`` 并登记 ``backup_record``。

        - ``VACUUM INTO`` 生成压缩一致快照（目标文件必须事先不存在，
          文件名含时间戳与随机 id 保证唯一）；
        - 生成后登记 CREATED，再以独立只读连接打开备份文件复验
          （integrity_check + 关键表行数），通过置 VERIFIED（填
          verified_at），失败置 FAILED；
        - db_schema_version 从当前库 local_meta/alembic_version 读取。
        """
        backup_id = f"bak-{uuid.uuid4().hex[:12]}"
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        dest_dir.mkdir(parents=True, exist_ok=True)
        backup_path = dest_dir / f"{_BACKUP_FILE_PREFIX}-{stamp}-{backup_id}.db"

        if not db_path.exists():
            return self._record_failed(
                backup_id=backup_id,
                backup_path=backup_path,
                backup_type=backup_type,
                db_schema_version="unknown",
                sha256="",
                size_bytes=0,
                error=f"当前数据库不存在：{db_path}",
            )

        try:
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(f"VACUUM INTO '{_quote_sqlite_path(backup_path)}'")
            sha256 = _sha256_file(backup_path)
            size_bytes = backup_path.stat().st_size
            with closing(_open_ro(db_path)) as meta_conn:
                db_schema_version = _read_schema_version(meta_conn)
        except sqlite3.Error as exc:
            return self._record_failed(
                backup_id=backup_id,
                backup_path=backup_path,
                backup_type=backup_type,
                db_schema_version="unknown",
                sha256="",
                size_bytes=0,
                error=f"备份生成失败（VACUUM INTO）：{exc}",
            )

        self._records.create(
            backup_id=backup_id,
            file_path=str(backup_path),
            backup_type=backup_type,
            db_schema_version=db_schema_version,
            sha256=sha256,
            size_bytes=size_bytes,
        )

        # 读回复验：独立只读连接打开备份文件本身
        try:
            with closing(_open_ro(backup_path)) as verify_conn:
                if not _integrity_ok(verify_conn):
                    raise sqlite3.DatabaseError("integrity_check 未通过")
                row_counts = _table_row_counts(verify_conn)
                if any(count < 0 for count in row_counts.values()):
                    missing = sorted(
                        name for name, count in row_counts.items() if count < 0
                    )
                    raise sqlite3.DatabaseError(f"关键表缺失：{missing}")
        except sqlite3.Error as exc:
            return self._record_failed(
                backup_id=backup_id,
                backup_path=backup_path,
                backup_type=backup_type,
                db_schema_version=db_schema_version,
                sha256=sha256,
                size_bytes=size_bytes,
                error=f"备份复验失败：{exc}",
            )

        self._records.mark_verified(backup_id)
        return BackupResult(
            backup_id=backup_id,
            file_path=backup_path,
            sha256=sha256,
            size_bytes=size_bytes,
            status=BACKUP_STATUS_VERIFIED,
            db_schema_version=db_schema_version,
            row_counts=row_counts,
        )

    # ------------------------------------------------------------------
    # 恢复（先校验后原子替换，失败零改动）
    # ------------------------------------------------------------------

    def restore(
        self,
        backup_path: Path,
        db_path: Path,
        safety_backup_dir: Path | None = None,
    ) -> RestoreResult:
        """把 ``backup_path`` 备份恢复为当前库 ``db_path``。

        流程（任一步失败立即返回，当前库零字节改动）：
        1. 备份文件存在 + 校验值比对（backup_record.sha256）；
        2. 复制到临时路径（与当前库同目录），独立连接校验
           integrity / alembic 版本一致 / 关键表行数可读；
        3. （可选）恢复前安全备份当前库到 ``safety_backup_dir``（AUTO）；
        4. 关闭连接池 → checkpoint 当前库 WAL → 清理 -wal/-shm →
           ``os.replace`` 原子替换。
        """
        if not backup_path.exists():
            return RestoreResult(
                backup_path=backup_path,
                success=False,
                error=f"备份文件不存在：{backup_path}",
            )
        if not db_path.exists():
            return RestoreResult(
                backup_path=backup_path,
                success=False,
                error=f"当前数据库不存在：{db_path}",
            )

        # 步骤 1：登记校验值比对（失败零写入——不触碰当前库）
        record = self._records.get_by_file_path(str(backup_path))
        if record is not None:
            actual = _sha256_file(backup_path)
            if actual != record.sha256:
                return RestoreResult(
                    backup_path=backup_path,
                    success=False,
                    error="备份文件校验失败：SHA-256 与登记记录不一致"
                    "（文件可能已损坏或被篡改），已放弃恢复，当前库未做任何改动",
                )

        # 步骤 2-4：临时路径贯穿（同目录保证原子替换且不跨卷）
        tmp_path = db_path.parent / f"{_RESTORE_TMP_PREFIX}-{uuid.uuid4().hex}.db"
        try:
            try:
                shutil.copy2(backup_path, tmp_path)
            except OSError as exc:
                return RestoreResult(
                    backup_path=backup_path,
                    success=False,
                    error=f"备份文件复制失败：{exc}，当前库未做任何改动",
                )
            check_error, row_counts = self._validate_restored(
                tmp_path=tmp_path, db_path=db_path
            )
            if check_error is not None:
                return RestoreResult(
                    backup_path=backup_path,
                    success=False,
                    error=check_error,
                )

            # 步骤 3：恢复前安全备份当前库（§35.9；失败则中止恢复）
            safety: BackupResult | None = None
            if safety_backup_dir is not None:
                safety = self.backup(
                    db_path, safety_backup_dir, backup_type=BACKUP_TYPE_AUTO
                )
                if safety.status != BACKUP_STATUS_VERIFIED:
                    return RestoreResult(
                        backup_path=backup_path,
                        success=False,
                        error=f"恢复前安全备份当前库失败，已中止恢复：{safety.error}",
                    )

            # 步骤 4：关闭连接池（Windows 文件锁）→ WAL checkpoint → 原子替换
            self._dispose_engine()
            if not self._atomic_replace(source=tmp_path, target=db_path):
                return RestoreResult(
                    backup_path=backup_path,
                    success=False,
                    safety_backup=safety,
                    error="恢复替换失败（无法原子替换当前库文件），当前库保持原样",
                )
            # 步骤 5：安全备份记录补登记（原记录随旧库被替换消失）
            if safety is not None:
                self._re_register_safety(safety)
            return RestoreResult(
                backup_path=backup_path,
                success=True,
                row_counts=row_counts,
                safety_backup=safety,
            )
        finally:
            # 成功时 tmp 已被 replace 消费；失败/异常时清理临时文件
            if tmp_path.exists():
                tmp_path.unlink()

    def list_backups(self, limit: int = 50) -> list[BackupRecordRow]:
        """最近备份记录（时间倒序，备份页列表展示）。"""
        return self._records.list_records(limit)

    def get_backup(self, backup_id: str) -> BackupRecordRow | None:
        """按 backup_id 查备份记录（恢复入口）。"""
        return self._records.get_by_backup_id(backup_id)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _re_register_safety(self, safety: BackupResult) -> None:
        """把恢复前安全备份的记录补登记进恢复后的库。

        安全备份生成于原子替换之前，其 ``backup_record`` 登记在被替换的
        旧库中，随替换消失；不补登记则备份文件成为孤儿（磁盘上存在但
        列表不显示、无法再从它恢复，“恢复前自动备份当前库”失去意义）。
        补登记沿用原 backup_id / 文件路径 / SHA-256 / AUTO 类型，状态直接
        置 VERIFIED（该文件在生成时已通过读回复验）。登记失败不影响
        已成功的恢复（文件仍在磁盘，下次备份/恢复可重新发现）。
        """
        try:
            if self._records.get_by_backup_id(safety.backup_id) is not None:
                return
            self._records.create(
                backup_id=safety.backup_id,
                file_path=str(safety.file_path),
                backup_type=BACKUP_TYPE_AUTO,
                db_schema_version=safety.db_schema_version,
                sha256=safety.sha256,
                size_bytes=safety.size_bytes,
                status=BACKUP_STATUS_VERIFIED,
                verified_at=utc_now_iso(),
            )
        except Exception:  # noqa: BLE001 —— 补登记失败不回滚已成功的恢复
            return

    def _record_failed(
        self,
        *,
        backup_id: str,
        backup_path: Path,
        backup_type: str,
        db_schema_version: str,
        sha256: str,
        size_bytes: int,
        error: str,
    ) -> BackupResult:
        """登记 FAILED 备份记录并返回失败结果（status=FAILED 路径）。"""
        try:
            self._records.create(
                backup_id=backup_id,
                file_path=str(backup_path),
                backup_type=backup_type,
                db_schema_version=db_schema_version,
                sha256=sha256 or "0" * 64,
                size_bytes=size_bytes,
            )
            self._records.mark_failed(backup_id)
        except Exception:  # noqa: BLE001 —— 登记失败不掩盖备份本身的错误
            error = f"{error}（且备份记录登记失败）"
        return BackupResult(
            backup_id=backup_id,
            file_path=backup_path,
            sha256=sha256,
            size_bytes=size_bytes,
            status=BACKUP_STATUS_FAILED,
            db_schema_version=db_schema_version,
            error=error,
        )

    def _validate_restored(
        self, tmp_path: Path, db_path: Path
    ) -> tuple[str | None, dict[str, int]]:
        """校验恢复后的临时库：integrity / alembic 版本一致 / 关键表行数可读。

        返回 (错误信息或 None, 关键表行数)；版本比对对象为当前库
        （恢复不得静默降级/升级 schema）。
        """
        try:
            with closing(_open_ro(tmp_path)) as restored, closing(
                _open_ro(db_path)
            ) as current:
                if not _integrity_ok(restored):
                    return (
                        (
                            "备份文件完整性校验失败（integrity_check 未通过），"
                            "已放弃恢复，当前库未做任何改动"
                        ),
                        {},
                    )
                restored_version = _read_alembic_version(restored)
                current_version = _read_alembic_version(current)
                if (
                    restored_version is not None
                    and current_version is not None
                    and restored_version != current_version
                ):
                    return (
                        (
                            f"备份 schema 版本不一致：备份为 {restored_version}、"
                            f"当前库为 {current_version}，已放弃恢复，当前库未做任何改动"
                        ),
                        {},
                    )
                row_counts = _table_row_counts(restored)
                if any(count < 0 for count in row_counts.values()):
                    missing = sorted(
                        name for name, count in row_counts.items() if count < 0
                    )
                    return (
                        (
                            f"备份文件关键表缺失：{missing}，"
                            "已放弃恢复，当前库未做任何改动"
                        ),
                        {},
                    )
                return None, row_counts
        except sqlite3.Error as exc:
            return (
                (
                    f"备份文件校验失败（无法作为数据库打开）：{exc}，"
                    "已放弃恢复，当前库未做任何改动"
                ),
                {},
            )

    def _atomic_replace(self, source: Path, target: Path) -> bool:
        """WAL checkpoint → 清理 -wal/-shm → os.replace 原子替换。"""
        try:
            # 合并当前库 WAL 到主文件，避免替换后遗留旧日志造成数据回放
            with closing(sqlite3.connect(target)) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            # checkpoint 失败（如库被锁）：不继续替换，保住当前库现状
            return False
        for suffix in ("-wal", "-shm"):
            side = target.with_name(target.name + suffix)
            if side.exists():
                side.unlink()
        try:
            os.replace(source, target)
        except OSError:
            return False
        return True

    def _dispose_engine(self) -> None:
        """关闭注入会话工厂绑定的连接池（Windows 上文件替换前置条件）。"""
        engine = self._session_factory.kw.get("bind")
        if isinstance(engine, Engine):
            engine.dispose()
