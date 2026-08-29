"""单实例文件锁占位测试（M0 只验证接口语义）。"""

from __future__ import annotations

from pathlib import Path

from local_data.single_instance import LOCK_FILE_NAME, SingleInstanceLock


def test_acquire_and_release(tmp_path: Path) -> None:
    """持锁创建锁文件，释放删除；重复 acquire 幂等，未持锁 release 空操作。"""
    lock = SingleInstanceLock(tmp_path)
    assert lock.acquire() is True
    assert (tmp_path / LOCK_FILE_NAME).is_file()
    assert lock.acquire() is True
    lock.release()
    assert not (tmp_path / LOCK_FILE_NAME).exists()
    lock.release()


def test_second_instance_is_blocked(tmp_path: Path) -> None:
    """单主工作台：第二个实例 acquire 返回 False，释放后可竞争获得。"""
    first = SingleInstanceLock(tmp_path)
    second = SingleInstanceLock(tmp_path)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True


def test_acquire_creates_missing_data_dir(tmp_path: Path) -> None:
    """数据目录不存在时 acquire 自动创建（含父目录）。"""
    lock = SingleInstanceLock(tmp_path / "deep" / "data")
    assert lock.acquire() is True
    assert lock.lock_path.is_file()
    lock.release()
