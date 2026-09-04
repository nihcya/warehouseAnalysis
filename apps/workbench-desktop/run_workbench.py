"""WarehouseWorkbench 打包入口（M3 Task 6）：绝对导入启动器。

PyInstaller 将入口脚本以 ``__main__`` 方式执行（无父包上下文），相对导入
不可用，因此 ``app/main.py``（包内相对导入）不能直接作为 spec 入口；本模块
经绝对导入 ``app.main`` 间接启动，效果与 ``python -m app.main`` 一致。
"""

from __future__ import annotations

from app.main import main

if __name__ == "__main__":
    raise SystemExit(main())
