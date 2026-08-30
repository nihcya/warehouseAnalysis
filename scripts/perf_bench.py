"""性能基准入口（M1：validate + analyze KPI + digest 计时）。

用法：
    uv run python scripts/perf_bench.py                 # 默认 1 万 / 10 万 / 100 万条
    uv run python scripts/perf_bench.py --size 10000    # 仅跑指定规模（可重复传入）

生成随机 movements（固定随机种子，只依赖标准库与已装依赖），分别计时
``WarehouseEngine.validate_dataset``、``WarehouseEngine.analyze``（含真实
KPI/COGS 计算；注意 analyze 内部会再执行一次校验）与 ``dataset_digest``
（result.build_input_summary），以 JSON 报告输出到 stdout。

阈值口径：
- M1：阈值建议基于本脚本实测基线设定（记录于 docs/m1-handover-b.md），
  脚本本身不设硬门禁；
- M3：冻结性能基线与 CI 硬件条件下的耗时上限（口径以 docs/formula-spec.md
  第 10 节"重复运行要求"与开发规划文档的性能验收为准）。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from contracts import (
    AnalysisRequest,
    EngineDataset,
    EventSource,
    MovementRecord,
    MoveType,
    SkuRecord,
)
from warehouse_engine import WarehouseEngine
from warehouse_engine.result import build_input_summary

START_DATE = date(2026, 6, 1)
END_DATE = date(2026, 6, 30)
PERIOD_DAYS = (END_DATE - START_DATE).days
SKU_COUNT = 50
WAREHOUSES = ("WH-01", "WH-02", "WH-03")
DEFAULT_SIZES = (10_000, 100_000, 1_000_000)
DEFAULT_SEED = 20260829


def build_request() -> AnalysisRequest:
    """构建基准用分析请求（期间与仓库范围固定）。"""
    return AnalysisRequest(
        run_id="run-perf-bench-0001",
        start_date=START_DATE,
        end_date=END_DATE,
        warehouse_ids=list(WAREHOUSES),
    )


def build_dataset(size: int, rng: random.Random) -> EngineDataset:
    """生成指定规模的随机 movements（日期均在期间内，事件 ID 唯一）。"""
    skus = [
        SkuRecord(
            sku_id=f"SKU-{index:05d}",
            name=f"SKU-{index:05d}",
            category="benchmark",
            unit="件",
            unit_cost=Decimal("1.20"),
        )
        for index in range(SKU_COUNT)
    ]
    base_at = datetime(2026, 6, 1, 8, 0, 0, tzinfo=UTC)
    movements: list[MovementRecord] = []
    append = movements.append
    for index in range(size):
        day_offset = rng.randrange(PERIOD_DAYS)
        append(
            MovementRecord(
                event_id=f"EVT-{index:08d}",
                sku_id=f"SKU-{rng.randrange(SKU_COUNT):05d}",
                move_type=MoveType.INBOUND if index % 2 == 0 else MoveType.OUTBOUND,
                quantity=Decimal(rng.randrange(1, 100)),
                move_date=START_DATE + timedelta(days=day_offset),
                occurred_at=base_at + timedelta(days=day_offset),
                warehouse_id=WAREHOUSES[index % len(WAREHOUSES)],
                source=EventSource.IMPORT,
            )
        )
    return EngineDataset(schema_version="1.0", skus=skus, movements=movements)


def main() -> None:
    """运行基准并输出 JSON 报告（stdout）。"""
    parser = argparse.ArgumentParser(description="M1 性能基准入口（validate/analyze/digest 计时）")
    parser.add_argument(
        "--size",
        type=int,
        action="append",
        default=None,
        metavar="N",
        help="movement 规模（可重复传入；缺省跑 1 万/10 万/100 万）",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"随机种子（默认 {DEFAULT_SEED}，固定以保证可复现）",
    )
    args = parser.parse_args()
    sizes = args.size if args.size else list(DEFAULT_SIZES)

    engine = WarehouseEngine()
    request = build_request()
    runs: list[dict[str, object]] = []
    for size in sizes:
        print(f"[perf_bench] size={size}: building dataset...", file=sys.stderr)
        rng = random.Random(args.seed)

        build_start = time.perf_counter()
        dataset = build_dataset(size, rng)
        build_seconds = time.perf_counter() - build_start

        print(f"[perf_bench] size={size}: validate_dataset...", file=sys.stderr)
        validate_start = time.perf_counter()
        report = engine.validate_dataset(request, dataset)
        validate_seconds = time.perf_counter() - validate_start

        print(f"[perf_bench] size={size}: analyze (KPI/COGS)...", file=sys.stderr)
        analyze_start = time.perf_counter()
        result = engine.analyze(request, dataset)
        analyze_seconds = time.perf_counter() - analyze_start

        print(f"[perf_bench] size={size}: dataset_digest...", file=sys.stderr)
        digest_start = time.perf_counter()
        build_input_summary(dataset, request)
        digest_seconds = time.perf_counter() - digest_start

        runs.append(
            {
                "size": size,
                "movement_count": len(dataset.movements),
                "issue_count": len(report.issues),
                "warning_count": len(report.warnings),
                "build_dataset_seconds": round(build_seconds, 4),
                "validate_dataset_seconds": round(validate_seconds, 4),
                "analyze_seconds": round(analyze_seconds, 4),
                "kpi_metric_count": len(result.metrics),
                "result_warning_count": len(result.warnings),
                "dataset_digest_seconds": round(digest_seconds, 4),
            }
        )

    report_payload = {
        "bench": "perf_bench",
        "stage": "M1-baseline",
        "engine_version": engine.engine_version,
        "formula_version": engine.formula_version,
        "seed": args.seed,
        "runs": runs,
        "thresholds": None,
        "note": "M1 基线不设硬门禁：阈值建议见 docs/m1-handover-b.md（实测留余量），M3 冻结。",
    }
    print(json.dumps(report_payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
