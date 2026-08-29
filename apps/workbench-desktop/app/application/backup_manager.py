"""备份管理用例（M1 Task 9）：包装 infrastructure 备份服务供 presentation 调用。

- presentation 只依赖本模块（不 import infrastructure，分层与导入页一致）；
- 手动备份 / 备份列表（时间、类型、大小、状态）/ 按 backup_id 恢复；
- 恢复走 BackupService 的安全流程（先校验后原子替换，失败当前库零改动），
  恢复前自动安全备份当前库（AUTO 类型，§35.9）；
- 结果均为含中文消息的状态结构，UI 直接回显。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_data.models import BACKUP_STATUS_VERIFIED, BackupRecordRow

from ..infrastructure.backup.backup_service import BackupService


@dataclass(frozen=True)
class BackupOperationStatus:
    """一次备份/恢复的结果（成功带补充信息，失败带中文消息）。"""

    ok: bool
    message: str


class BackupManager:
    """备份管理用例：手动备份、列表展示与安全恢复。"""

    def __init__(
        self,
        service: BackupService,
        db_path: Path,
        backup_dir: Path,
    ) -> None:
        self._service = service
        self._db_path = db_path
        self._backup_dir = backup_dir

    @property
    def backup_dir(self) -> Path:
        """备份文件输出目录（备份页“打开所在目录”）。"""
        return self._backup_dir

    def run_manual_backup(self) -> BackupOperationStatus:
        """手动备份当前库（MANUAL 类型）。"""
        result = self._service.backup(self._db_path, self._backup_dir)
        if result.status != BACKUP_STATUS_VERIFIED:
            return BackupOperationStatus(ok=False, message=f"备份失败：{result.error}")
        return BackupOperationStatus(
            ok=True,
            message=(
                f"备份完成（{result.backup_id}）：{result.file_path}"
                f"（{result.size_bytes} 字节，schema {result.db_schema_version}）"
            ),
        )

    def list_backups(self) -> list[dict[str, Any]]:
        """备份列表（时间/类型/大小/状态，新 → 旧，供表格展示）。"""
        return [
            {
                "backup_id": record.backup_id,
                "file_path": record.file_path,
                "created_at": record.created_at,
                "backup_type": record.backup_type,
                "size_bytes": record.size_bytes,
                "status": record.status,
                "verified_at": record.verified_at,
                "db_schema_version": record.db_schema_version,
            }
            for record in self._service.list_backups()
        ]

    def get_backup(self, backup_id: str) -> BackupRecordRow | None:
        """按 backup_id 查备份记录（恢复入口）。"""
        return self._service.get_backup(backup_id)

    def restore(self, backup_id: str) -> BackupOperationStatus:
        """恢复指定备份（恢复前自动安全备份当前库，失败当前库零改动）。"""
        record = self._service.get_backup(backup_id)
        if record is None:
            return BackupOperationStatus(ok=False, message=f"未找到备份记录：{backup_id}")
        backup_path = Path(record.file_path)
        if not backup_path.exists():
            return BackupOperationStatus(
                ok=False,
                message=f"备份文件不存在：{backup_path}",
            )
        result = self._service.restore(
            backup_path, self._db_path, safety_backup_dir=self._backup_dir
        )
        if not result.success:
            return BackupOperationStatus(ok=False, message=result.error or "恢复失败")
        safety_note = ""
        if result.safety_backup is not None:
            safety_note = f"；恢复前已安全备份当前库（{result.safety_backup.backup_id}）"
        return BackupOperationStatus(
            ok=True,
            message=(
                f"恢复完成：{backup_path.name}，关键表行数 "
                f"{result.row_counts}{safety_note}。建议重启工作台以刷新页面数据。"
            ),
        )
