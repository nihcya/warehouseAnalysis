"""升级安全用例（M3 Task 5）：升级前带版本标记的备份 + 版本门控。

复用 ``BackupService``（VACUUM INTO 全量快照 + backup_record 登记），
备份类型登记为 AUTO；应用版本写入消息文本，便于在备份页与发布清单中
追溯到"该备份由哪个版本升级前打出"。

版本门控：上次运行版本记录在数据目录 ``app_meta.json``（组合根在迁移
成功后写入）；``version_changed_since_last_run`` 供组合根判断"上次运行
版本 != 当前版本（含无记录）"时才做升级前备份，避免同版本每次启动都
全量快照。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from local_data.models import BACKUP_STATUS_VERIFIED, BACKUP_TYPE_AUTO

from ..infrastructure.backup.backup_service import BackupService

#: 版本记录文件名（数据目录/app_meta.json）
APP_META_FILENAME = "app_meta.json"

#: 版本记录键名
LAST_RUN_VERSION_KEY = "last_run_version"


def version_record_path(data_dir: Path) -> Path:
    """版本记录文件路径：``<数据目录>/app_meta.json``。"""
    return data_dir / APP_META_FILENAME


def read_last_run_version(data_dir: Path) -> str | None:
    """读上次运行版本；文件缺失/损坏/键缺失返回 ``None``（视为未知版本）。"""
    try:
        payload = json.loads(version_record_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = payload.get(LAST_RUN_VERSION_KEY) if isinstance(payload, dict) else None
    return version if isinstance(version, str) and version else None


def write_last_run_version(data_dir: Path, app_version: str) -> None:
    """写上次运行版本（迁移成功后由组合根调用；失败仅吞掉——丢失的代价
    只是下次启动多备一次快照，安全侧倾斜）。"""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        version_record_path(data_dir).write_text(
            json.dumps({LAST_RUN_VERSION_KEY: app_version}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def version_changed_since_last_run(data_dir: Path, app_version: str) -> bool:
    """上次运行版本 != 当前版本（含无记录/损坏）→ 需要升级前备份。"""
    return read_last_run_version(data_dir) != app_version


@dataclass(frozen=True)
class PreUpgradeBackupStatus:
    """升级前备份结果（成功带文件路径，失败带中文消息）。"""

    ok: bool
    message: str
    backup_path: Path | None = None


def run_pre_upgrade_backup(
    service: BackupService,
    db_path: Path,
    backup_dir: Path,
    app_version: str,
) -> PreUpgradeBackupStatus:
    """升级（覆盖安装后首次启动的迁移）前对当前库做一次全量备份。

    :param service: 备份服务（组合根已注入会话工厂，迁移前会话可用）。
    :param db_path: 当前业务库文件路径。
    :param backup_dir: 备份输出目录（与数据目录同级）。
    :param app_version: 当前应用版本，写入结果消息作版本标记。
    :return: 备份结果；失败时 ``ok=False`` 且 message 含原因。
    """
    result = service.backup(db_path, backup_dir, backup_type=BACKUP_TYPE_AUTO)
    if result.status != BACKUP_STATUS_VERIFIED:
        return PreUpgradeBackupStatus(
            ok=False, message=f"升级前备份失败：{result.error}"
        )
    return PreUpgradeBackupStatus(
        ok=True,
        message=(
            f"升级前备份完成（v{app_version}）：{result.file_path}"
            f"（{result.size_bytes} 字节，schema {result.db_schema_version}）"
        ),
        backup_path=result.file_path,
    )
