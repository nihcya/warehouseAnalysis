#!/usr/bin/env python3
"""端到端验证：建仓库 -> 导入 SKU 主数据 -> 导入库存事件 -> 跑 M2 分析。

本脚本不依赖仓库内置样例数据：先用同目录 ``gen_mock_data.py`` 在本地生成
1000 条事件 + 20 条主数据，再走完整工作台导入/分析链路，最后断言 18 个指标齐全
（9 KPI + 9 M2），验证 B 侧 M2 引擎（WarehouseEngine 0.3.0）在真实工作台里可用。

数据目录隔离在 ``--data-dir``（默认系统临时目录），不污染正在运行的工作台。

运行（在 apps/workbench-desktop 下，uv 环境）：
    cd apps/workbench-desktop
    uv run python tests/run_mock_test.py
可选：
    uv run python tests/run_mock_test.py --data-dir /tmp/wb_mock_test --csv-dir /tmp/wb_csv
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_ROOT = HERE.parent  # apps/workbench-desktop
sys.path.insert(0, str(APP_ROOT))  # 允许 import app
sys.path.insert(0, str(HERE))  # 允许 import gen_mock_data

from gen_mock_data import generate

# (仓库编号, 名称)；mock 数据引用的仓库必须先建立
WAREHOUSES = [("WH-01", "中心仓"), ("WH-02", "华东仓"), ("WH-03", "华南仓")]

# 默认本地数据目录与 CSV 目录（均临时，避免污染）
DEFAULT_DATA_DIR = tempfile.mkdtemp(prefix="wb_mock_test_")
DEFAULT_CSV_DIR = tempfile.mkdtemp(prefix="wb_mock_csv_")


def main() -> None:
    ap = argparse.ArgumentParser(description="M2 引擎端到端验证（建仓库+导入+分析）")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="WORKBENCH_DATA_DIR（本地库目录）")
    ap.add_argument("--csv-dir", default=DEFAULT_CSV_DIR, help="生成 mock CSV 的目录")
    args = ap.parse_args()

    csv_dir = Path(args.csv_dir)
    data_dir = Path(args.data_dir)

    print("== 0. 生成 mock 数据（确定性） ==")
    events_csv, master_csv = generate(csv_dir)
    print(f"   事件 CSV : {events_csv}")
    print(f"   主数据 CSV: {master_csv}")

    # 必须在 import app 之前设定（create_session_factory 会读取）
    os.environ["WORKBENCH_DATA_DIR"] = str(data_dir)
    os.environ["WORKBENCH_ENGINE"] = "local"  # 真实 M2 引擎 0.3.0

    from app.application.analysis_usecase import RunAnalysisUseCase
    from app.application.import_manager import (
        EVENT_OPTIONAL_FIELDS,
        EVENT_REQUIRED_FIELDS,
        IMPORT_TYPE_EVENTS,
        IMPORT_TYPE_MASTER,
        MASTER_OPTIONAL_FIELDS,
        MASTER_REQUIRED_FIELDS,
        CsvImportManager,
    )
    from app.infrastructure.db.result_store import SqlResultStore
    from app.main import (
        create_dataset_provider,
        create_engine_provider,
        create_session_factory,
    )
    from local_data.repository import MasterDataRepository

    def build_mapping(header, required, optional):
        return {f: f for f in list(required) + list(optional) if f in header}

    print("== 1. 初始化本地库（Alembic 迁移到 head） ==")
    session_factory = create_session_factory()

    print("== 2. 建立仓库（mock 数据引用的 WH-01/02/03） ==")
    repo = MasterDataRepository(session_factory)
    for wid, name in WAREHOUSES:
        if repo.get_warehouse_by_warehouse_id(wid) is None:
            repo.add_warehouse(warehouse_id=wid, name=name)
            print(f"   已建仓库 {wid} ({name})")

    print("== 3. 导入 SKU 主数据 ==")
    mgr = CsvImportManager(session_factory)
    m_header = master_csv.read_text(encoding="utf-8").splitlines()[0].split(",")
    r1 = mgr.run_import(
        path=master_csv,
        import_type=IMPORT_TYPE_MASTER,
        mapping=build_mapping(m_header, MASTER_REQUIRED_FIELDS, MASTER_OPTIONAL_FIELDS),
    )
    print(f"   主数据: inserted={r1.inserted} skipped={r1.skipped} error={r1.error_count} batch={r1.batch_id}")
    assert r1.error_count == 0, f"主数据导入有错误: {r1.error_count}"

    print("== 4. 导入库存事件（mock.csv，1000 条） ==")
    e_header = events_csv.read_text(encoding="utf-8").splitlines()[0].split(",")
    r2 = mgr.run_import(
        path=events_csv,
        import_type=IMPORT_TYPE_EVENTS,
        mapping=build_mapping(e_header, EVENT_REQUIRED_FIELDS, EVENT_OPTIONAL_FIELDS),
    )
    print(f"   事件:   inserted={r2.inserted} skipped={r2.skipped} error={r2.error_count} batch={r2.batch_id} status={r2.status}")
    assert r2.error_count == 0, f"事件导入有错误: {r2.error_count}"
    assert r2.status == "COMPLETED", f"事件导入批次状态异常: {r2.status}"

    print("== 5. 运行 M2 分析（真实 WarehouseEngine 0.3.0） ==")
    engine = create_engine_provider()
    store = SqlResultStore(session_factory)
    provider = create_dataset_provider(session_factory)
    use_case = RunAnalysisUseCase(engine, store, dataset_provider=provider)
    outcome = use_case.run()

    assert not outcome.no_data, "分析返回 no_data=True（事件未成功入库，数据链路断裂）"
    assert outcome.ok, f"校验失败: {[(i.code, i.message) for i in outcome.issues]}"

    result = outcome.result
    metrics = result.metrics
    fids = sorted(m.formula_id for m in metrics)
    print(f"   ✅ 分析成功 run_id={result.run_id}")
    print(f"   指标总数: {len(metrics)}")
    print("   指标 formula_id 列表:")
    for fid in fids:
        print(f"     - {fid}")

    # 断言 18 指标齐全（8 KPI + 1 COGS + 9 M2）
    expected_kpi = {
        "F-KPI-001", "F-KPI-002", "F-KPI-003", "F-KPI-004", "F-KPI-005",
        "F-KPI-006", "F-KPI-007", "F-KPI-008", "F-COGS-001",
    }
    expected_m2 = {
        "F-ABC-001", "F-AGE-001", "F-STALE-001",
        "F-REPL-001", "F-REPL-002", "F-REPL-003",
        "F-FCST-001", "F-FCST-002", "F-BM-001",
    }
    got = set(fids)
    missing_kpi = expected_kpi - got
    missing_m2 = expected_m2 - got
    assert not missing_kpi, f"缺失 KPI 指标: {missing_kpi}"
    assert not missing_m2, f"缺失 M2 指标: {missing_m2}"
    assert len(metrics) == 18, f"指标数应为 18，实际 {len(metrics)}"

    warnings = getattr(result, "warnings", [])
    print(f"   分析警告数: {len(warnings)}")
    print("✅ 端到端验证通过：建仓库→导入→M2分析 18 指标齐全。")


if __name__ == "__main__":
    main()
