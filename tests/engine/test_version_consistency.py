"""版本一致性守护测试（M3 新增）：防止多版本源漂移。

背景：M2 交付时 ``packages/warehouse-engine/pyproject.toml`` 仍为 0.2.0
（M1 遗留），而 ``__version__.py`` 已升至 0.3.0——wheel 以 pyproject 为
版本源构建，漂移将产出与运行时版本矛盾的发布产物。本测试把多个版本源
锁在一起，任何单一源未随版本提升同步更新都会在 CI 当场失败：

1. engine 包 ``pyproject.toml``（wheel 构建版本源）
2. ``warehouse_engine.__version__.ENGINE_VERSION``（运行时版本）
3. ``tests/engine/test_golden.py`` 硬编码断言的引擎版本
4. ``docs/compatibility-matrix.md`` Engine 行

另校验 contracts 包 pyproject 与 engine pyproject 的版本对齐关系
（两包同仓库同步演进，M1/M2 均同版本），以及 formula_version 冻结值。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from warehouse_engine.__version__ import ENGINE_VERSION, FORMULA_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_TEST_PATH = REPO_ROOT / "tests" / "engine" / "test_golden.py"
MATRIX_PATH = REPO_ROOT / "docs" / "compatibility-matrix.md"


def _read_pyproject_version(pyproject: Path) -> str:
    with pyproject.open("rb") as handle:
        data = tomllib.load(handle)
    version = data["project"]["version"]
    assert isinstance(version, str)
    return version


def test_engine_pyproject_matches_engine_version() -> None:
    """pyproject（wheel 版本源）必须与运行时 ENGINE_VERSION 一致。"""
    pyproject = REPO_ROOT / "packages" / "warehouse-engine" / "pyproject.toml"
    assert _read_pyproject_version(pyproject) == ENGINE_VERSION


def test_contracts_pyproject_matches_engine_version() -> None:
    """contracts-python 与 warehouse-engine 同步演进（同版本发布）。"""
    pyproject = REPO_ROOT / "packages" / "contracts-python" / "pyproject.toml"
    assert _read_pyproject_version(pyproject) == ENGINE_VERSION


def test_golden_assertion_matches_engine_version() -> None:
    """黄金数据测试断言的 engine_version 与运行时版本一致。

    test_golden.py 对 result.engine_version 的硬编码断言是黄金验收的
    版本锚点；若仅提升运行时版本而漏改断言（或反之），此处当场失败。
    """
    text = GOLDEN_TEST_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"result\.engine_version\s*==\s*\"([^\"]+)\"", text
    )
    assert match is not None, "test_golden.py 缺少 engine_version 断言"
    assert match.group(1) == ENGINE_VERSION


def test_compatibility_matrix_engine_row() -> None:
    """兼容矩阵 Engine 行的当前版本必须与运行时版本一致。"""
    text = MATRIX_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"^\|\s*Engine\s*\|\s*`([^`]+)`", text, flags=re.MULTILINE
    )
    assert match is not None, "兼容矩阵缺少 Engine 行"
    assert match.group(1) == ENGINE_VERSION


def test_formula_version_frozen() -> None:
    """公式口径版本冻结在 0.1.0；变更口径必须走 A/B 评审流程。"""
    assert FORMULA_VERSION == "0.1.0"
