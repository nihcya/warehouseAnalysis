"""staging 验证（M3 spec R3：版本兼容 / 回滚演练 / 结果可追溯）。

用法（在 workspace 内执行）：

    uv run python scripts/staging_verify.py [--staging-dir <dir>] [--no-offline]

流程（一次运行完成 R3 全部三项验证，产物落 ``--staging-dir``）：

1. **脱敏数据集**：以固定种子合成 staging 数据集（真实分布特征：多仓多品类、
   工作日加权出库、退货冲减、批次成本、期末盘点、缺参数与无基准场景；
   全合成、不含任何真实客户数据），写出 ``staging-dataset.json`` 与指纹
   ``staging-fingerprint.json``（行数 / 字段 / SHA-256）；
2. **版本兼容（R3.1）**：staging venv 安装 0.3.0 双 wheel → Skill manifest
   的 ``engine_version_range`` 全部命中 → 黄金 v0.1.0 完整冒烟 →
   staging 数据集分析结果归档（engine_version / formula_version /
   dataset_digest 齐全）；
3. **回滚演练（R3.2）**：降级安装 0.2.0 双 wheel → manifest 范围检查
   （kpi 命中、四个 0.3.0-only Skill 被拒绝）→ 已装 contracts 0.2.0
   导出的 JSON Schema 与仓库快照零 diff → 黄金 v0.1.0 / v0.2.0 在
   0.2.0 下 KPI/COGS 冻结值通过 → staging 数据集 0.2.0 结果归档；
   再升级回 0.3.0 复跑黄金冒烟与 staging 分析（字节级复现第一次结果）；
4. **结果可追溯（R3.3）**：双版本 staging 结果对比——共享的 9 个
   KPI/COGS 指标值一致（formula_version 0.1.0 未变），dataset_digest
   一致；差异逐条列出（0.3.0 新增 9 指标、真实告警取代占位告警）。

退出码非 0 即验证失败；每步命令与结论由本脚本 stdout 记录，
归档供 ``docs/m3-staging-log-b.md`` 引用。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"
MANIFEST_PATH = REPO_ROOT / "skills" / "manifest.json"
SCHEMA_DIR = REPO_ROOT / "packages" / "contracts-schema"
GOLDEN_ROOT = REPO_ROOT / "tests" / "fixtures" / "golden"

#: staging 数据集合成的固定种子（指纹可复现的前提）
STAGING_SEED = 20260906

#: 0.2.0 wheel 的候选目录（回滚演练用；M1 构建于 worktree dist/）
DIST_020_CANDIDATES = (
    DIST_DIR,
    REPO_ROOT.parent / "warehouseAnalysis-b-m1" / "dist",
)

#: M1（0.2.0）analyze 输出的 9 个 KPI/COGS 指标
M1_METRIC_IDS = frozenset(f"F-KPI-{i:03d}" for i in range(1, 9)) | {"F-COGS-001"}

#: §10 容差表（与 tests/engine/conftest.py 同口径）
TOLERANCE_TABLE: dict[str, tuple[str, str]] = {
    "money": ("0.01", "1e-6"),
    "qty": ("0.001", "1e-9"),
    "ratio": ("1e-6", "1e-6"),
}


# ---------------------------------------------------------------------------
# 脱敏 staging 数据集（合成，固定种子）
# ---------------------------------------------------------------------------

def build_staging_payload() -> dict[str, Any]:
    """合成脱敏 staging 数据集（request + dataset 载荷，契约 JSON 形态）。

    分布特征：2 仓库 × 4 品类 × 12 SKU、历史两批入库（不同成本）、
    工作日加权出库、偶发退货、期末盘点、10/12 SKU 有补货参数
    （2 缺 → PARAM_MISSING）、基准仅覆盖 2 品类（另 2 品类 →
    BENCHMARK_UNAVAILABLE）。全部字段由本函数合成，无任何真实客户数据。
    """
    rng = random.Random(STAGING_SEED)
    start = date(2026, 6, 1)
    end = date(2026, 7, 31)
    warehouses = ("WH-01", "WH-02")
    catalog = [
        ("SKU-STG-0001", "澄源饮用水 550ml", "饮料", "瓶", "1.20"),
        ("SKU-STG-0002", "山麦苏打饼干 150g", "零食", "包", "3.50"),
        ("SKU-STG-0003", "素色纯棉毛巾", "家纺", "条", "9.80"),
        ("SKU-STG-0004", "多效地板清洁剂 1L", "清洁用品", "瓶", "12.00"),
        ("SKU-STG-0005", "冷萃乌龙茶 500ml", "饮料", "瓶", "4.00"),
        ("SKU-STG-0006", "海盐薯片 100g", "零食", "袋", "4.50"),
        ("SKU-STG-0007", "抗菌抹布 3 片装", "清洁用品", "包", "6.30"),
        ("SKU-STG-0008", "全棉床单单人款", "家纺", "件", "49.00"),
        ("SKU-STG-0009", "维C气泡水 330ml", "饮料", "罐", "2.80"),
        ("SKU-STG-0010", "坚果混合装 200g", "零食", "袋", "15.80"),
        ("SKU-STG-0011", "洗衣液补充装 2kg", "清洁用品", "袋", "19.90"),
        ("SKU-STG-0012", "亚麻桌布 140cm", "家纺", "条", "35.00"),
    ]

    skus = [
        {
            "sku_id": sku_id,
            "name": name,
            "category": category,
            "unit": unit,
            "unit_cost": cost,
            "currency": "CNY",
        }
        for sku_id, name, category, unit, cost in catalog
    ]

    movements: list[dict[str, Any]] = []
    event_seq = 0
    balance: dict[tuple[str, str], int] = {}

    def _add(sku_id: str, move_type: str, qty: int, day: date, wh: str, cost: str | None) -> None:
        nonlocal event_seq
        event_seq += 1
        record: dict[str, Any] = {
            "event_id": f"EVT-STG-{event_seq:05d}",
            "sku_id": sku_id,
            "move_type": move_type,
            "quantity": str(qty),
            "move_date": day.isoformat(),
            "occurred_at": f"{day.isoformat()}T{8 + event_seq % 9:02d}:30:00Z",
            "warehouse_id": wh,
            "source": "IMPORT" if move_type == "INBOUND" else "MINI_PROGRAM",
        }
        if cost is not None:
            record["unit_cost"] = cost
        movements.append(record)
        delta = qty if move_type in ("INBOUND", "RETURN") else -qty
        balance[(sku_id, wh)] = balance.get((sku_id, wh), 0) + delta

    for sku_id, _, _, _, cost in catalog:
        for wh in warehouses:
            _add(sku_id, "INBOUND", rng.randrange(120, 240), date(2026, 5, 8), wh, cost)
            _add(sku_id, "INBOUND", rng.randrange(40, 90), date(2026, 5, 22), wh,
                 str(round(float(cost) * 1.05, 2)))

    day = start
    while day < end:
        weekday = day.weekday()
        outbound_prob = 0.60 if weekday < 5 else 0.22
        for sku_id, _, _, _, _ in catalog:
            for wh in warehouses:
                if rng.random() < outbound_prob:
                    qty = rng.randrange(1, 9)
                    if balance.get((sku_id, wh), 0) >= qty + 5:
                        _add(sku_id, "OUTBOUND", qty, day, wh, None)
                if rng.random() < 0.035:
                    _add(sku_id, "RETURN", rng.randrange(1, 3), day, wh, None)
        day += timedelta(days=1)

    snapshots = []
    for sku_id, _, _, _, cost in catalog:
        for wh in warehouses:
            qty = balance.get((sku_id, wh), 0)
            snapshots.append(
                {
                    "sku_id": sku_id,
                    "snapshot_date": "2026-07-30",
                    "quantity": str(qty),
                    "warehouse_id": wh,
                    "inventory_value": str(round(qty * float(cost), 2)),
                }
            )

    replenishment = [
        {
            "sku_id": sku_id,
            "avg_daily_demand": str(round(rng.uniform(1.5, 6.0), 2)),
            "lead_time_days": str(rng.randrange(3, 15)),
            "service_level": "0.95",
        }
        for sku_id, _, _, _, _ in catalog
        if sku_id not in ("SKU-STG-0010", "SKU-STG-0011")
    ]

    return {
        "request": {
            "schema_version": "1.0",
            "run_id": "run-staging-m3-0001",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "warehouse_ids": list(warehouses),
            "parameters": {
                "service_level": "0.95",
                "industry": "综合零售",
                "benchmarks": [
                    {
                        "source": "中国仓储与配送协会（staging 合成）",
                        "region": "全国",
                        "industry": "综合零售",
                        "sample_scope": "staging 合成样本（脱敏）",
                        "updated_at": "2026-06-01",
                        "benchmark_version": "1.1.0",
                        "unit": "次/期间",
                        "applicability": "staging 验证用合成基准，仅覆盖饮料/零食品类",
                        "metric": "KPI.TURNOVER",
                        "value": "1.10",
                    }
                ],
            },
        },
        "dataset": {
            "schema_version": "1.0",
            "skus": skus,
            "movements": movements,
            "snapshots": snapshots,
            "replenishment": replenishment,
        },
    }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_dataset(staging_dir: Path) -> Path:
    """写出脱敏数据集与指纹，返回数据集路径。"""
    payload = build_staging_payload()
    dataset_path = staging_dir / "staging-dataset.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    dataset_path.write_text(text, encoding="utf-8")

    dataset = payload["dataset"]
    fingerprint = {
        "run_id": payload["request"]["run_id"],
        "de-identification": (
            f"全合成数据（scripts/staging_verify.py build_staging_payload，"
            f"种子 {STAGING_SEED}），无任何真实客户数据"
        ),
        "period": [payload["request"]["start_date"], payload["request"]["end_date"]],
        "warehouses": payload["request"]["warehouse_ids"],
        "sku_count": len(dataset["skus"]),
        "movement_count": len(dataset["movements"]),
        "snapshot_count": len(dataset["snapshots"]),
        "replenishment_count": len(dataset["replenishment"]),
        "movement_fields": sorted(dataset["movements"][0]),
        "payload_sha256": _sha256_text(text),
    }
    (staging_dir / "staging-fingerprint.json").write_text(
        json.dumps(fingerprint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"[staging] 脱敏数据集：{fingerprint['sku_count']} SKU / "
        f"{fingerprint['movement_count']} movements / {fingerprint['snapshot_count']} snapshots，"
        f"sha256={fingerprint['payload_sha256'][:16]}…"
    )
    return dataset_path


# ---------------------------------------------------------------------------
# 版本范围检查（Skill manifest）
# ---------------------------------------------------------------------------

def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def in_range(version: str, spec: str) -> bool:
    """判断版本是否命中 ``>=a,<b`` 形式的逗号分隔范围。"""
    for clause in spec.split(","):
        clause = clause.strip()
        if clause.startswith(">="):
            if _version_tuple(version) < _version_tuple(clause[2:]):
                return False
        elif clause.startswith("<"):
            if _version_tuple(version) >= _version_tuple(clause[1:]):
                return False
        elif clause.startswith("=="):
            if _version_tuple(version) != _version_tuple(clause[2:]):
                return False
        else:
            raise ValueError(f"不支持的范围子句：{clause!r}（spec={spec!r}）")
    return True


def check_manifest(engine_version: str, expect_hit: set[str]) -> None:
    """校验 manifest 各 Skill 的 engine_version_range 命中情况符合预期。"""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    all_names = {skill["name"] for skill in manifest["skills"]}
    hits = {
        skill["name"]
        for skill in manifest["skills"]
        if in_range(engine_version, skill["engine_version_range"])
    }
    assert hits == expect_hit, (
        f"engine {engine_version} 命中 {sorted(hits)}，预期 {sorted(expect_hit)}"
    )
    rejected = sorted(all_names - hits)
    print(
        f"[staging] manifest 校验：engine {engine_version} 命中 {sorted(hits)}，"
        f"拒绝 {rejected or '无'}"
    )


# ---------------------------------------------------------------------------
# 子进程模式（由 staging venv 的解释器执行，仅依赖标准库 + 已装 wheel）
# ---------------------------------------------------------------------------

def _tolerance_ok(expected: str, actual: str, kind: str) -> bool:
    from decimal import Decimal

    abs_tol, rel_tol = (Decimal(v) for v in TOLERANCE_TABLE[kind])
    diff = abs(Decimal(actual) - Decimal(expected))
    return diff <= abs_tol or diff <= rel_tol * abs(Decimal(expected))


def child_smoke_020(golden_dir: Path) -> None:
    """0.2.0 冒烟：9 个 KPI/COGS 冻结值通过 + 四类占位告警在列。"""
    from importlib import metadata

    from contracts import AnalysisRequest, EngineDataset
    from warehouse_engine import WarehouseEngine

    payload = json.loads((golden_dir / "input.json").read_text(encoding="utf-8"))
    expected = json.loads((golden_dir / "expected.json").read_text(encoding="utf-8"))
    request = AnalysisRequest.model_validate(payload["request"])
    dataset = EngineDataset.model_validate(payload["dataset"])
    result = WarehouseEngine().analyze(request, dataset)

    assert result.engine_version == metadata.version("warehouse-engine") == "0.2.0"
    assert result.formula_version == "0.1.0"
    metric_ids = {m.formula_id for m in result.metrics}
    assert metric_ids == M1_METRIC_IDS, f"0.2.0 指标 {sorted(metric_ids)}"
    assert "ANALYSIS_PLACEHOLDER" in [w.code for w in result.warnings], (
        "0.2.0 应对四类未实装公式输出 ANALYSIS_PLACEHOLDER"
    )

    by_id = {m.formula_id: m for m in result.metrics}
    checked = 0
    for formula_id, item in expected["metrics"].items():
        if formula_id not in M1_METRIC_IDS:
            continue
        metric = by_id[formula_id]
        assert metric.formula_version == "0.1.0", formula_id
        assert _tolerance_ok(item["value"], str(metric.value), item["tolerance"]), (
            f"{formula_id}：期望 {item['value']}，实际 {metric.value}"
        )
        checked += 1
    assert checked == len(M1_METRIC_IDS), f"黄金冻结的 KPI/COGS 覆盖不足：{checked}"
    print(f"[staging] 0.2.0 黄金冒烟通过：{golden_dir.name}，{checked} 项 KPI/COGS 冻结值一致")


def child_dump_schemas() -> None:
    """导出已装 contracts 的两份 JSON Schema（stdout，父进程与仓库快照比对）。"""
    from contracts import AnalysisRequest, AnalysisResult

    print(json.dumps(
        {
            "analysis-request.schema.json": AnalysisRequest.model_json_schema(),
            "analysis-result.schema.json": AnalysisResult.model_json_schema(),
        },
        ensure_ascii=False,
    ))


def child_analyze(payload_path: Path, out_path: Path) -> None:
    """以当前已装 engine 分析 staging 数据集并归档结果。"""
    from importlib import metadata

    from contracts import AnalysisRequest, EngineDataset
    from warehouse_engine import WarehouseEngine

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    request = AnalysisRequest.model_validate(payload["request"])
    dataset = EngineDataset.model_validate(payload["dataset"])
    result = WarehouseEngine().analyze(request, dataset)
    out_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(
        f"[staging] 分析归档：engine {result.engine_version}（wheel "
        f"{metadata.version('warehouse-engine')}）/ formula {result.formula_version} / "
        f"{len(result.metrics)} 指标 / digest {result.input_summary.dataset_digest[:16]}… → {out_path.name}"
    )


# ---------------------------------------------------------------------------
# 驱动模式
# ---------------------------------------------------------------------------

def _find_uv() -> str:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("未找到 uv：staging 验证需要 uv 创建独立 venv。")
    return uv


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / "Scripts" / "python.exe"


def _run(cmd: list[str], **kwargs: Any) -> None:
    subprocess.run(cmd, check=True, capture_output=True, **kwargs)


def _child_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"}
    }


def _install_wheels(uv: str, venv_dir: Path, wheels: list[Path], offline: bool) -> None:
    cmd = ["uv", "pip", "install", "--python", str(_venv_python(venv_dir))]
    if offline:
        cmd.append("--offline")
    cmd.extend(str(w) for w in wheels)
    _run(cmd)


def _wheels(dist_dir: Path, version: str) -> list[Path]:
    contracts = dist_dir / f"contracts_python-{version}-py3-none-any.whl"
    engine = dist_dir / f"warehouse_engine-{version}-py3-none-any.whl"
    missing = [p.name for p in (contracts, engine) if not p.exists()]
    if missing:
        raise FileNotFoundError(f"{dist_dir} 缺少 wheel：{missing}")
    return [contracts, engine]


def _find_dist_020() -> Path:
    for candidate in DIST_020_CANDIDATES:
        if (candidate / "warehouse_engine-0.2.0-py3-none-any.whl").exists():
            return candidate
    raise SystemExit(
        "未找到 0.2.0 wheel（回滚演练需要）：请在 --staging 数据目录外提供 M1 构建的 "
        f"dist（候选：{[str(c) for c in DIST_020_CANDIDATES]}）。"
    )


def _run_child(venv_dir: Path, args: list[str]) -> None:
    """以 staging venv 解释器运行本脚本的子模式（输出透传）。"""
    subprocess.run(
        [str(_venv_python(venv_dir)), str(Path(__file__).resolve()), *args],
        check=True,
        env=_child_env(),
    )


def _run_wheel_smoke(venv_dir: Path, golden_dir: Path) -> None:
    """以 staging venv 解释器运行 scripts/wheel_smoke.py 完整冒烟（0.3.0）。"""
    wheel_smoke = Path(__file__).resolve().parent / "wheel_smoke.py"
    subprocess.run(
        [str(_venv_python(venv_dir)), str(wheel_smoke), "--smoke-only",
         "--golden", str(golden_dir)],
        check=True,
        env=_child_env(),
    )


def _compare_schemas(venv_dir: Path) -> None:
    """已装 contracts（0.2.0）导出的 Schema 与仓库快照零 diff。"""
    completed = subprocess.run(
        [str(_venv_python(venv_dir)), str(Path(__file__).resolve()), "--child", "dump-schemas"],
        check=True,
        capture_output=True,
        env=_child_env(),
    )
    dumped = json.loads(completed.stdout.decode("utf-8"))
    for filename, schema in dumped.items():
        on_disk = json.loads((SCHEMA_DIR / filename).read_text(encoding="utf-8"))
        assert on_disk == schema, f"{filename} 与 0.2.0 contracts 导出不一致"
    print("[staging] Schema 快照比对：0.2.0 contracts 导出与 packages/contracts-schema/ 零 diff")


def _compare_traceability(staging_dir: Path) -> None:
    """R3.3：双版本 staging 结果对比（共享指标一致 + 差异逐条列出）。"""
    r020 = json.loads((staging_dir / "result-engine-0.2.0.json").read_text(encoding="utf-8"))
    r030 = json.loads((staging_dir / "result-engine-0.3.0.json").read_text(encoding="utf-8"))

    assert r020["engine_version"] == "0.2.0" and r030["engine_version"] == "0.3.0"
    assert r020["formula_version"] == r030["formula_version"] == "0.1.0"
    assert r020["input_summary"]["dataset_digest"] == r030["input_summary"]["dataset_digest"], (
        "同一数据集的 dataset_digest 跨版本不一致，可追溯性破坏"
    )

    m020 = {m["formula_id"]: m for m in r020["metrics"]}
    m030 = {m["formula_id"]: m for m in r030["metrics"]}
    assert set(m020) == M1_METRIC_IDS, f"0.2.0 指标集异常：{sorted(set(m020))}"

    diffs: list[str] = []
    for formula_id in sorted(set(m020) & set(m030)):
        if m020[formula_id]["value"] != m030[formula_id]["value"]:
            diffs.append(
                f"{formula_id}: 0.2.0={m020[formula_id]['value']} ≠ 0.3.0={m030[formula_id]['value']}"
            )
    assert not diffs, f"公式口径未变但共享指标不一致：{diffs}"

    added = sorted(set(m030) - set(m020))
    w020 = [w["code"] for w in r020["warnings"]]
    w030 = [w["code"] for w in r030["warnings"]]
    print(
        f"[staging] 可追溯对比：9 个 KPI/COGS 指标值完全一致；dataset_digest 一致"
        f"（{r030['input_summary']['dataset_digest'][:16]}…）"
    )
    print("[staging] 差异清单（引擎行为差异，非口径变更）：")
    print(f"  - 0.3.0 新增指标 {len(added)} 项：{added}")
    print(f"  - 0.2.0 告警含 ANALYSIS_PLACEHOLDER {w020.count('ANALYSIS_PLACEHOLDER')} 条；"
          f"0.3.0 为真实告警 {sorted(set(w030))}")


def run_driver(args: argparse.Namespace) -> None:
    uv = _find_uv()
    staging_dir = args.staging_dir or Path(tempfile.gettempdir()) / "wh-staging-m3"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)
    venv_dir = staging_dir / "venv"

    print(f"[staging] staging 目录：{staging_dir}")

    # ---- R3.0 脱敏数据集 ----
    dataset_path = write_dataset(staging_dir)

    wheels_030 = _wheels(DIST_DIR, "0.3.0")
    dist_020 = _find_dist_020()
    wheels_020 = _wheels(dist_020, "0.2.0")
    print(f"[staging] 0.2.0 回滚 wheel 来源：{dist_020}")

    # ---- R3.1 版本兼容（0.3.0）----
    print("[staging] === R3.1 版本兼容（0.3.0）===")
    _run([uv, "venv", "--python", "3.11", str(venv_dir)])
    _install_wheels(uv, venv_dir, wheels_030, args.offline)
    check_manifest("0.3.0", expect_hit={"kpi", "abc-aging", "replenishment", "forecasting", "benchmark"})
    _run_wheel_smoke(venv_dir, GOLDEN_ROOT / "v0.1.0")
    _run_child(venv_dir, ["--child", "analyze", "--payload", str(dataset_path),
                          "--out", str(staging_dir / "result-engine-0.3.0.json")])

    # ---- R3.2 回滚演练（0.2.0 → 0.3.0）----
    print("[staging] === R3.2 回滚演练（降级 0.2.0）===")
    _install_wheels(uv, venv_dir, wheels_020, args.offline)
    check_manifest("0.2.0", expect_hit={"kpi"})
    _compare_schemas(venv_dir)
    _run_child(venv_dir, ["--child", "smoke-020", "--golden", str(GOLDEN_ROOT / "v0.1.0")])
    _run_child(venv_dir, ["--child", "smoke-020", "--golden", str(GOLDEN_ROOT / "v0.2.0")])
    _run_child(venv_dir, ["--child", "analyze", "--payload", str(dataset_path),
                          "--out", str(staging_dir / "result-engine-0.2.0.json")])

    print("[staging] === R3.2 回滚演练（升回 0.3.0 复验）===")
    _install_wheels(uv, venv_dir, wheels_030, args.offline)
    _run_wheel_smoke(venv_dir, GOLDEN_ROOT / "v0.1.0")
    replay_path = staging_dir / "result-engine-0.3.0-replay.json"
    _run_child(venv_dir, ["--child", "analyze", "--payload", str(dataset_path), "--out", str(replay_path)])
    first = (staging_dir / "result-engine-0.3.0.json").read_bytes()
    assert replay_path.read_bytes() == first, "升回 0.3.0 后结果与首次运行不一致（复现失败）"
    print("[staging] 升回 0.3.0：黄金冒烟通过，staging 结果与首次运行字节一致")

    # ---- R3.3 结果可追溯 ----
    print("[staging] === R3.3 结果可追溯（双版本对比）===")
    _compare_traceability(staging_dir)

    print("[staging] PASS：staging 三项验证（兼容 / 回滚 / 可追溯）全部通过")


def main() -> None:
    parser = argparse.ArgumentParser(description="M3 staging 验证（兼容 / 回滚 / 可追溯）")
    parser.add_argument("--staging-dir", type=Path, default=None, help="staging 产物目录（默认临时目录）")
    parser.add_argument(
        "--no-offline", dest="offline", action="store_false",
        help="允许联网解析依赖（默认 --offline 从 uv 缓存解析）",
    )
    parser.add_argument("--child", choices=["smoke-020", "dump-schemas", "analyze"],
                        help="（内部）子模式，由 staging venv 解释器调用")
    parser.add_argument("--golden", type=Path, default=None, help="黄金数据目录（子模式用）")
    parser.add_argument("--payload", type=Path, default=None, help="staging 数据集路径（子模式用）")
    parser.add_argument("--out", type=Path, default=None, help="结果输出路径（子模式用）")
    args = parser.parse_args()

    if args.child == "smoke-020":
        assert args.golden is not None
        child_smoke_020(args.golden)
    elif args.child == "dump-schemas":
        child_dump_schemas()
    elif args.child == "analyze":
        assert args.payload is not None and args.out is not None
        child_analyze(args.payload, args.out)
    else:
        run_driver(args)


if __name__ == "__main__":
    main()
