"""工作台启动自动种子 WH-01 基础仓库的回归测试。

回归背景：首次启动空库迁移后 warehouse 表为空，导入库存事件 CSV 时
每行仓库存在性检查 WAREHOUSE_NOT_FOUND 全部失败（23 行全败）。
组合根 ``create_session_factory`` 在 Alembic 迁移完成后幂等种子 WH-01。
"""

from __future__ import annotations

from pathlib import Path

from local_data.models import WarehouseRow
from local_data.repository import MasterDataRepository
from sqlalchemy import func, select
from workbench.main import create_session_factory


def _count_warehouses(session_factory) -> int:
    """统计 warehouse 表行数（断言“不重复创建”用）。"""
    with session_factory() as session:  # type: Session
        return session.execute(select(func.count()).select_from(WarehouseRow)).scalar_one()


def test_seed_creates_wh01_on_fresh_db(tmp_path: Path) -> None:
    """空库迁移后首次启动应自动种子 WH-01。"""
    data_dir = tmp_path / "wb-data"
    session_factory = create_session_factory(data_dir)

    repo = MasterDataRepository(session_factory)
    row = repo.get_warehouse_by_warehouse_id("WH-01")
    assert row is not None
    assert row.name == "主仓"
    # warehouse 表仅 WH-01 一行
    assert _count_warehouses(session_factory) == 1


def test_seed_idempotent_when_wh01_exists(tmp_path: Path) -> None:
    """WH-01 已存在时不重复创建（幂等）。"""
    data_dir = tmp_path / "wb-data"
    # 第一次启动：种子 WH-01
    session_factory = create_session_factory(data_dir)
    repo = MasterDataRepository(session_factory)
    assert repo.get_warehouse_by_warehouse_id("WH-01") is not None

    # 第二次启动（同一库已存在 WH-01）：不应重复创建
    session_factory_2 = create_session_factory(data_dir)
    repo_2 = MasterDataRepository(session_factory_2)
    row = repo_2.get_warehouse_by_warehouse_id("WH-01")
    assert row is not None
    assert row.name == "主仓"
    # 仍仅 WH-01 一行
    assert _count_warehouses(session_factory_2) == 1
