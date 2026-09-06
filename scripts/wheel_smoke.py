"""独立环境 wheel 冒烟验证（M3 发布产物，spec R2.1 / Task 3）。

用法（驱动模式，在 workspace 内执行）：

    uv run python scripts/wheel_smoke.py [--dist-dir dist]
                                        [--golden tests/fixtures/golden/v0.1.0]
                                        [--venv-dir <dir>] [--no-offline]

流程：

1. 在 ``--venv-dir``（默认 ``%TEMP%\\wh-wheel-smoke-<engine版本>``）用
   ``uv venv`` 创建全新 venv；
2. 仅安装 ``--dist-dir`` 下的 contracts-python 与 warehouse-engine 两个
   wheel（默认 ``--offline``：依赖从 uv 缓存解析，不联网）；
3. 以该 venv 的解释器运行本脚本的冒烟模式（``--smoke-only``）：加载黄金
   数据，断言 wheel 自洽。

冒烟模式（由 venv 解释器执行）只依赖标准库与已安装的两个 wheel，不导入
workspace 任何源码；并断言 ``warehouse_engine`` 解析自 venv 的
site-packages，证明被验证对象是 wheel 而非源码。断言内容：

- 版本自洽：``result.engine_version`` == 已装 warehouse-engine 的
  ``importlib.metadata`` 版本 == 已装 contracts-python 版本；
  ``result.formula_version`` == ``0.1.0``（口径冻结不变）；
- 指标完备：18 个 formula_id 恰好各一个，无 ANALYSIS_PLACEHOLDER；
- 数据质量：data_quality 分组的告警码集合与黄金 expected 冻结值一致；
- 确定性：同一输入连续两次 ``analyze`` 序列化字节一致；
- 可追溯：input_summary 的 dataset_digest / 数量统计与输入一致。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"
DEFAULT_GOLDEN = REPO_ROOT / "tests" / "fixtures" / "golden" / "v0.1.0"

#: M2 后 analyze 恒输出的指标总数（五类公式 18 指标）
EXPECTED_METRIC_COUNT = 18
FROZEN_FORMULA_VERSION = "0.1.0"


def _find_uv() -> str:
    """定位 uv 可执行文件（驱动模式依赖）。"""
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("未找到 uv：驱动模式需要 uv 创建独立 venv 并安装 wheel。")
    return uv


def _venv_python(venv_dir: Path) -> Path:
    """venv 内 Python 解释器路径（Windows 布局）。"""
    return venv_dir / "Scripts" / "python.exe"


def _wheel_paths(dist_dir: Path) -> tuple[Path, Path]:
    """定位 dist/ 下两个 0.3.0 wheel（本脚本不挑版本，按名称模式匹配最新）。"""
    contracts = sorted(dist_dir.glob("contracts_python-*.whl"))
    engine = sorted(dist_dir.glob("warehouse_engine-*.whl"))
    if not contracts or not engine:
        raise SystemExit(f"dist/ 下未找到 wheel（{dist_dir}）；请先运行 uv build。")
    return contracts[-1], engine[-1]


def _engine_version_of(wheel_path: Path) -> str:
    """从 wheel 文件名解析版本（warehouse_engine-<version>-py3-none-any.whl）。"""
    return wheel_path.stem.split("-")[1]


def run_driver(args: argparse.Namespace) -> None:
    """驱动模式：建 venv、装 wheel、以 venv 解释器跑冒烟模式。"""
    uv = _find_uv()
    contracts_wheel, engine_wheel = _wheel_paths(args.dist_dir)
    engine_version = _engine_version_of(engine_wheel)

    venv_dir = args.venv_dir or Path(tempfile.gettempdir()) / f"wh-wheel-smoke-{engine_version}"
    if venv_dir.exists():
        shutil.rmtree(venv_dir)

    print(f"[wheel-smoke] 创建独立 venv：{venv_dir}")
    subprocess.run(
        [uv, "venv", "--python", "3.11", str(venv_dir)],
        check=True,
        capture_output=True,
    )

    install_cmd = [uv, "pip", "install", "--python", str(_venv_python(venv_dir))]
    if args.offline:
        install_cmd.append("--offline")
    install_cmd.extend([str(contracts_wheel), str(engine_wheel)])
    print(f"[wheel-smoke] 仅安装两个 wheel：{contracts_wheel.name} + {engine_wheel.name}")
    subprocess.run(install_cmd, check=True, capture_output=True)

    # 清理环境变量，确保子进程不继承 workspace 的任何 Python 路径
    child_env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"}
    }
    print(f"[wheel-smoke] 以 venv 解释器运行冒烟断言（黄金数据 {args.golden}）")
    subprocess.run(
        [str(_venv_python(venv_dir)), str(Path(__file__).resolve()), "--smoke-only",
         "--golden", str(args.golden)],
        check=True,
        env=child_env,
    )
    print(f"[wheel-smoke] PASS：wheel {engine_version} 独立环境冒烟全部通过")


def run_smoke(golden_dir: Path) -> None:
    """冒烟模式：仅依赖标准库与已装 wheel，断言 wheel 自洽。"""
    # 延迟导入：确保解析自当前解释器（venv）的 site-packages
    from importlib import metadata

    import warehouse_engine
    from contracts import AnalysisRequest, EngineDataset
    from warehouse_engine import WarehouseEngine

    # 证明被验证对象是 wheel：模块必须解析自当前解释器前缀之下
    engine_module_path = Path(warehouse_engine.__file__).resolve()
    assert engine_module_path.is_relative_to(Path(sys.prefix)), (
        f"warehouse_engine 解析自 {engine_module_path}，不在 venv（{sys.prefix}）内："
        "冒烟必须针对 wheel，而非 workspace 源码。"
    )

    payload = json.loads((golden_dir / "input.json").read_text(encoding="utf-8"))
    expected = json.loads((golden_dir / "expected.json").read_text(encoding="utf-8"))
    request = AnalysisRequest.model_validate(payload["request"])
    dataset = EngineDataset.model_validate(payload["dataset"])

    result = WarehouseEngine().analyze(request, dataset)

    # 版本自洽（wheel 内三处版本源一致）
    wheel_engine_version = metadata.version("warehouse-engine")
    wheel_contracts_version = metadata.version("contracts-python")
    assert result.engine_version == wheel_engine_version, (
        f"engine_version={result.engine_version}，wheel 元数据={wheel_engine_version}"
    )
    assert wheel_contracts_version == wheel_engine_version, (
        f"contracts wheel={wheel_contracts_version}，engine wheel={wheel_engine_version}"
    )
    assert result.formula_version == FROZEN_FORMULA_VERSION

    # 指标完备（M2 后 18 项；黄金 v0.1.0 输入同样适用完整引擎）
    formula_ids = [metric.formula_id for metric in result.metrics]
    assert len(formula_ids) == len(set(formula_ids)) == EXPECTED_METRIC_COUNT, (
        f"指标数 {len(formula_ids)}（去重 {len(set(formula_ids))}），应为 {EXPECTED_METRIC_COUNT}"
    )
    assert "ANALYSIS_PLACEHOLDER" not in [w.code for w in result.warnings]

    # 数据质量：analyze 告警序列与黄金冻结值逐条一致（含顺序），
    # data_quality 按码分组恰为该序列的去重集合
    frozen_codes = [item["code"] for item in expected["analyze_warnings"]]
    assert [w.code for w in result.warnings] == frozen_codes, (
        f"analyze 告警 {[w.code for w in result.warnings]}，黄金冻结 {frozen_codes}"
    )
    assert result.data_quality is not None, "data_quality 报告缺失"
    quality_codes = {entry.code for entry in result.data_quality}
    assert quality_codes == set(frozen_codes), (
        f"data_quality 码 {sorted(quality_codes)}，黄金冻结 {sorted(set(frozen_codes))}"
    )

    # 确定性：同一输入两次 analyze 序列化字节一致
    first = result.model_dump_json()
    second = WarehouseEngine().analyze(request, dataset).model_dump_json()
    assert first == second, "两次运行序列化结果不一致"

    # 可追溯：输入摘要与输入数据一致
    assert result.input_summary.movement_count == len(dataset.movements)
    assert result.input_summary.snapshot_count == len(dataset.snapshots)
    assert result.input_summary.dataset_digest

    print(
        f"[wheel-smoke] 冒烟通过：engine {result.engine_version} / "
        f"formula {result.formula_version} / 指标 {len(formula_ids)} 项 / "
        f"digest {result.input_summary.dataset_digest[:16]}…"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="独立环境 wheel 冒烟验证（M3）")
    parser.add_argument("--dist-dir", type=Path, default=DIST_DIR, help="wheel 所在目录")
    parser.add_argument(
        "--golden", type=Path, default=DEFAULT_GOLDEN, help="黄金数据目录（input.json/expected.json）"
    )
    parser.add_argument("--venv-dir", type=Path, default=None, help="独立 venv 目录（默认临时目录）")
    parser.add_argument(
        "--no-offline", dest="offline", action="store_false",
        help="允许联网解析依赖（默认 --offline 从 uv 缓存解析）",
    )
    parser.add_argument(
        "--smoke-only", action="store_true",
        help="（内部）冒烟模式：由独立 venv 的解释器调用，勿手动使用",
    )
    args = parser.parse_args()

    if args.smoke_only:
        run_smoke(args.golden)
    else:
        run_driver(args)


if __name__ == "__main__":
    main()
