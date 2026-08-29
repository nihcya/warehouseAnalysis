"""单实例文件锁占位（M0）。

主基线 §35.3：单主工作台只允许一个写入进程。M0 以极简
``O_CREAT | O_EXCL`` 文件锁占位（data_dir/.workbench.lock）；
进程崩溃遗留锁文件的检测与清理策略（进程存活探测）在 M1+ 补齐，
M0 只提供 acquire/release 接口与测试。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 锁文件名（位于数据目录下）
LOCK_FILE_NAME = ".workbench.lock"


class SingleInstanceLock:
    """数据目录级单实例锁：acquire 成功即持有，release 删除锁文件。"""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._lock_path = data_dir / LOCK_FILE_NAME
        self._fd: int | None = None

    @property
    def lock_path(self) -> Path:
        """锁文件路径。"""
        return self._lock_path

    def acquire(self) -> bool:
        """尝试持锁：成功返回 True；已被其他实例持有时返回 False。"""
        if self._fd is not None:
            return True  # 幂等：同一实例重复 acquire 视为成功
        self._data_dir.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        self._fd = fd
        try:
            # 锁文件内容写 PID，仅供诊断（M0 不做存活探测）
            os.write(fd, str(os.getpid()).encode("ascii"))
        except OSError:
            pass  # 写 PID 失败不影响持锁语义
        return True

    def release(self) -> None:
        """释放锁：关闭句柄并删除锁文件；未持锁时为空操作。"""
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        os.close(fd)
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            pass  # 锁文件已被外部清理时容忍
