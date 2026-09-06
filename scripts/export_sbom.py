"""导出 engine 域 SBOM 片段与锁定依赖清单（M3 发布产物，spec R2.3/R2.4）。

用法：``uv run python scripts/export_sbom.py``

从当前已解析环境（``importlib.metadata``）读取 warehouse-engine、
contracts-python 及其传递依赖的实际锁定版本，不联网。两个发布包自身的
版本以各自 ``pyproject.toml`` 为准（与 wheel 构建的版本源一致），并与
运行时 ``ENGINE_VERSION`` 交叉核对，不一致即失败（版本源漂移防回归，
对应 M3 spec §2 的 P0 修复背景）。

产物（写入 ``dist/``，不入库）：

- ``sbom-engine-<version>.json``：CycloneDX 1.5 JSON，覆盖两个发布包及
  其全部运行时依赖（组件含 name/version/purl）；
- ``requirements-engine-<version>.txt``：pip freeze 风格的锁定依赖摘要
  （不含两个发布包自身），头部标注 Python 版本与生成时间。
"""

from __future__ import annotations

import argparse
import json
import platform
import tomllib
import uuid
from collections import deque
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from warehouse_engine.__version__ import ENGINE_VERSION, FORMULA_VERSION

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"

#: SBOM 覆盖的两个发布包：(名称, 包目录)
RELEASE_PACKAGES: tuple[tuple[str, Path], ...] = (
    ("warehouse-engine", REPO_ROOT / "packages" / "warehouse-engine"),
    ("contracts-python", REPO_ROOT / "packages" / "contracts-python"),
)

CYCLONEDX_SPEC_VERSION = "1.5"
TOOL_NAME = "warehouse-analysis:scripts/export_sbom.py"
TOOL_VERSION = "1.0.0"


def _pyproject_version(package_dir: Path) -> str:
    """读取包 pyproject.toml 声明的版本（wheel 构建的版本源）。"""
    data = tomllib.loads((package_dir / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _release_versions() -> dict[str, str]:
    """两个发布包的 pyproject 版本，并校验与 ENGINE_VERSION 一致。"""
    versions = {name: _pyproject_version(package_dir) for name, package_dir in RELEASE_PACKAGES}
    engine_version = versions["warehouse-engine"]
    if engine_version != ENGINE_VERSION:
        raise SystemExit(
            "版本源漂移：warehouse-engine pyproject 声明 "
            f"{engine_version}，但运行时 ENGINE_VERSION 为 {ENGINE_VERSION}；"
            "请先对齐版本源再导出 SBOM。"
        )
    return versions


def _purl(name: str, version: str) -> str:
    """PyPI purl：``pkg:pypi/<规范化名>@<版本>``。"""
    return f"pkg:pypi/{canonicalize_name(name)}@{version}"


def _resolve_closure(root_names: list[str]) -> dict[str, metadata.Distribution]:
    """BFS 解析运行时依赖闭包（从已解析环境读取，不联网）。

    跳过 extras 条件依赖（标记含 ``extra == "..."`` 的可选依赖不随基础
    安装），标记不满足当前解释器环境的依赖同样跳过，与 uv/pip 的
    基础安装解析口径一致。
    """
    resolved: dict[str, metadata.Distribution] = {}
    queue: deque[str] = deque(root_names)
    while queue:
        name = queue.popleft()
        key = canonicalize_name(name)
        if key in resolved:
            continue
        dist = metadata.distribution(name)
        resolved[key] = dist
        for requirement_text in dist.requires or []:
            requirement = Requirement(requirement_text)
            if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
                continue
            queue.append(requirement.name)
    return resolved


def build_sbom() -> dict[str, object]:
    """构建 CycloneDX 1.5 SBOM（两个发布包 + 运行时依赖闭包）。"""
    versions = _release_versions()
    closure = _resolve_closure([name for name, _ in RELEASE_PACKAGES])

    components: list[dict[str, str]] = []
    for key in sorted(closure):
        dist = closure[key]
        # 发布包版本以 pyproject 为准（wheel 版本源）；第三方依赖用环境锁定版本
        version = versions.get(dist.metadata["Name"], dist.version)
        components.append(
            {
                "type": "library",
                "name": dist.metadata["Name"],
                "version": version,
                "purl": _purl(dist.metadata["Name"], version),
                "scope": "required",
            }
        )

    engine_version = versions["warehouse-engine"]
    return {
        "$schema": "http://cyclonedx.org/schema/bom-1.5.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": CYCLONEDX_SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "lifecycles": [{"phase": "post-build"}],
            "tools": {
                "components": [
                    {"type": "application", "name": TOOL_NAME, "version": TOOL_VERSION}
                ]
            },
            "component": {
                "type": "application",
                "name": "warehouse-engine",
                "version": engine_version,
                "purl": _purl("warehouse-engine", engine_version),
            },
            "properties": [
                {"name": "engine:formula-version", "value": FORMULA_VERSION},
                {"name": "engine:python-version", "value": platform.python_version()},
            ],
        },
        "components": components,
    }


def build_requirements() -> str:
    """构建锁定依赖清单文本（pip freeze 风格，不含两个发布包自身）。"""
    versions = _release_versions()
    engine_version = versions["warehouse-engine"]
    contracts_version = versions["contracts-python"]
    closure = _resolve_closure([name for name, _ in RELEASE_PACKAGES])
    root_keys = {canonicalize_name(name) for name, _ in RELEASE_PACKAGES}

    generated_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    lines = [
        f"# warehouse-engine {engine_version} / contracts-python {contracts_version} 锁定依赖摘要",
        f"# Python {platform.python_version()}；由 uv.lock 解析环境导出（scripts/export_sbom.py），不联网",
        f"# 生成时间 {generated_at}；两个发布包自身以 dist/ 产物分发，不在本清单",
    ]
    for key in sorted(closure):
        if key in root_keys:
            continue
        dist = closure[key]
        lines.append(f"{dist.metadata['Name']}=={dist.version}")
    return "\n".join(lines) + "\n"


def main() -> None:
    """导出 SBOM 与依赖清单到 dist/ 并打印写出路径。"""
    parser = argparse.ArgumentParser(
        description="导出 engine 域 SBOM（CycloneDX JSON）与锁定依赖清单（M3 发布产物）"
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DIST_DIR,
        help=f"产物输出目录（默认 {DIST_DIR}）",
    )
    args = parser.parse_args()

    engine_version = _release_versions()["warehouse-engine"]
    args.dist_dir.mkdir(parents=True, exist_ok=True)

    sbom_path = args.dist_dir / f"sbom-engine-{engine_version}.json"
    sbom_path.write_text(
        json.dumps(build_sbom(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已写出：{sbom_path}")

    requirements_path = args.dist_dir / f"requirements-engine-{engine_version}.txt"
    requirements_path.write_text(build_requirements(), encoding="utf-8")
    print(f"已写出：{requirements_path}")


if __name__ == "__main__":
    main()
