"""SBOM 导出结构与内容守护测试（M3 新增，spec R2.4）。

以 in-process 方式加载 ``scripts/export_sbom.py``（scripts 非 Python 包，
用 ``importlib.util`` 按路径加载），校验：

- **结构**：bomFormat / specVersion / serialNumber / metadata / components 齐备，
  符合 CycloneDX 1.5 顶层字段；
- **组件覆盖**：两个发布包（版本取自 pyproject，与 ``ENGINE_VERSION``
  一致）与核心运行时依赖（pandas/numpy/scipy/statsmodels/pydantic）均在列；
- **组件字段**：每个组件均含 name / version / purl（``pkg:pypi/`` 前缀、
  规范名@版本），且规范化名无重复；
- **依赖清单**：pip freeze 风格行、不含两个发布包自身、含核心依赖锁定版本。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

from warehouse_engine.__version__ import ENGINE_VERSION, FORMULA_VERSION

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "export_sbom.py"

#: 核心运行时依赖（engine + contracts 的直接依赖）
EXPECTED_CORE_DEPS = ("pandas", "numpy", "scipy", "statsmodels", "pydantic")

_REQUIREMENT_LINE = re.compile(r"[A-Za-z0-9._-]+==[A-Za-z0-9.+!-]+")


def _load_export_sbom() -> ModuleType:
    """按路径加载 scripts/export_sbom.py（scripts 目录非 Python 包）。"""
    spec = importlib.util.spec_from_file_location("export_sbom", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: 模块级加载一次：两个被测函数共用同一份闭包解析逻辑
export_sbom = _load_export_sbom()


def _canonical(name: str) -> str:
    """与 packaging.canonicalize_name 等价的简化规范化（测试内不引入 packaging）。"""
    return name.lower().replace("_", "-")


def test_sbom_structure() -> None:
    sbom = export_sbom.build_sbom()

    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.5"
    assert sbom["version"] == 1
    assert isinstance(sbom["serialNumber"], str)
    assert sbom["serialNumber"].startswith("urn:uuid:")
    assert "$schema" in sbom

    sbom_metadata = sbom["metadata"]
    assert isinstance(sbom_metadata, dict)
    assert sbom_metadata["component"]["name"] == "warehouse-engine"
    properties = {item["name"]: item["value"] for item in sbom_metadata["properties"]}
    assert properties["engine:formula-version"] == FORMULA_VERSION


def test_sbom_components_cover_release_packages_and_core_deps() -> None:
    components = export_sbom.build_sbom()["components"]
    by_name = {_canonical(component["name"]): component for component in components}

    # 组件名无重复（闭包 BFS 按规范化名去重的守护）
    assert len(by_name) == len(components)

    # 两个发布包在列，版本为 pyproject 版本源且与 ENGINE_VERSION 一致
    assert by_name["warehouse-engine"]["version"] == ENGINE_VERSION
    assert by_name["contracts-python"]["version"] == export_sbom._release_versions()[
        "contracts-python"
    ]

    # 核心运行时依赖在列
    for dep in EXPECTED_CORE_DEPS:
        assert _canonical(dep) in by_name, f"SBOM 缺少核心依赖 {dep}"


def test_sbom_component_fields() -> None:
    components = export_sbom.build_sbom()["components"]
    for component in components:
        assert component["type"] == "library"
        assert component["name"]
        assert component["version"]
        purl = component["purl"]
        assert purl.startswith("pkg:pypi/")
        # purl 尾部须为 规范名@版本，与组件字段一致
        tail = purl.removeprefix("pkg:pypi/")
        purl_name, _, purl_version = tail.partition("@")
        assert purl_name == _canonical(component["name"])
        assert purl_version == component["version"]


def test_requirements_manifest() -> None:
    text = export_sbom.build_requirements()
    lines = [line for line in text.splitlines() if line and not line.startswith("#")]

    assert lines, "依赖清单为空"
    for line in lines:
        assert _REQUIREMENT_LINE.fullmatch(line), f"非 pip freeze 风格行：{line}"

    names = {line.split("==", 1)[0].lower() for line in lines}
    # 两个发布包以 dist/ 产物分发，不应出现在依赖清单中
    assert "warehouse-engine" not in names
    assert "contracts-python" not in names
    for dep in EXPECTED_CORE_DEPS:
        assert dep in names, f"依赖清单缺少核心依赖 {dep}"
