"""一次性测试准备：在本地库预置 WH-01 仓库（事件导入的存在性校验依赖）。"""
from local_data.connection import connect
from local_data.repository import MasterDataRepository

engine, session_factory = connect()
repo = MasterDataRepository(session_factory)
if repo.get_warehouse_by_warehouse_id("WH-01") is None:
    repo.add_warehouse(warehouse_id="WH-01", name="主仓")
print("WH-01 ready:", repo.get_warehouse_by_warehouse_id("WH-01") is not None)
