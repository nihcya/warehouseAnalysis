# -*- mode: python ; coding: utf-8 -*-
"""WarehouseWorkbench 打包描述（M3 Task 6，spec「安装包」需求）。

用法（仓库根，uv 环境已安装 pyinstaller dev 依赖）::

    uv run python scripts/build_release.py            # 推荐：完整发布流水线
    uv run pyinstaller --noconfirm apps/workbench-desktop/workbench.spec

产物：``dist/WarehouseWorkbench/``（``--onedir`` 布局，主程序
``WarehouseWorkbench.exe`` + ``_internal/`` 资源目录，GUI 无控制台）。

关键约定（与 ``app/main.py`` 的 ``REPO_ROOT`` 冻结分支互相咬合）：

- ``app/main.py`` 在 ``sys.frozen`` 下把 ``REPO_ROOT`` 解析为 ``_internal``
  （``sys._MEIPASS``），因此本 spec 的 datas 必须把 ``local-data`` 与
  ``tests/fixtures`` 放到 ``_internal`` 下的同名相对路径；
- ``local-data/alembic/versions/*.py`` 是 Alembic 运行时按文件系统加载的
  迁移脚本（非 import 模块），必须作为 datas 收集，运行时经
  ``LOCAL_DATA_DIR / "alembic"``（= ``_internal/local-data/alembic``）定位
  （见 ``app/main.py`` 的 ``create_session_factory``）；
- 用户数据目录逻辑不受打包影响：仍在 ``%LOCALAPPDATA%\\WarehouseWorkbench``
  （``local_data.connection.resolve_data_dir``，安装目录只存程序）。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

# spec 文件所在目录（PyInstaller 注入的 SPECPATH），据此推导仓库根
SPEC_DIR = Path(SPECPATH)
# apps/workbench-desktop -> apps -> 仓库根
REPO_ROOT = SPEC_DIR.parents[1]

datas = [
    # Alembic 迁移脚本（env.py / script.py.mako / versions/*.py）：
    # 迁移脚本经文件系统读取而非 import，必须整目录打入 _internal/local-data/alembic
    (str(REPO_ROOT / "local-data" / "alembic"), "local-data/alembic"),
    # alembic.ini（CLI 调试对齐用；运行时编程构造 Config 不读取，随包保持目录完整）
    (str(REPO_ROOT / "local-data" / "alembic.ini"), "local-data"),
    # 基准数据 / FakeEngine 冻结结果 / golden 输入（main.py 与 analysis_usecase
    # 的 REPO_ROOT 相对路径解析，frozen 下落到 _internal/tests/fixtures）
    (str(REPO_ROOT / "tests" / "fixtures"), "tests/fixtures"),
]
# alembic 自带资源（templates 等，供扩展使用；upgrade 本身不依赖）
datas += collect_data_files("alembic")

a = Analysis(
    # 入口：绝对导入薄启动器（PyInstaller 以 __main__ 执行入口脚本，包内相对
    # 导入不可用，故不能直接用 app/main.py；run_workbench.py 等价 python -m app.main）
    [str(SPEC_DIR / "run_workbench.py")],
    # pathex 指向 apps/workbench-desktop，使 `app` 包可被定位导入
    pathex=[str(SPEC_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # GUI 应用不携带的标准库模块（减小体积，均为确认未被引用项）
        "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    # 产物名（同步约束 scripts/installer/warehouse-workbench.iss 的 AppExeName）
    name="WarehouseWorkbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # windowed：GUI 应用，无控制台窗口
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    # 目录名与 exe 名保持一致（Inno Setup [Files] 引用 dist/WarehouseWorkbench/*）
    name="WarehouseWorkbench",
)
