"""重复运行确定性测试（M3 新增，spec R1.1）。

需求来源：``docs/开发需求-B引擎Skill.md`` §7 M3 第 1 条「重复运行测试」。
两个层面：

1. **同进程重复运行**：同一输入连续 3 次 analyze，序列化结果逐字节一致
   （覆盖全部 18 指标与数据质量报告，证明无随机性/时间依赖/哈希遍历序
   泄漏——集合或字典的迭代顺序差异都会在这里暴露）。
2. **跨进程稳定**：子进程以相同输入独立 analyze，与父进程输出逐字节
   一致（排除 PYTHONHASHSEED 等进程级随机源影响；子进程以 ``-B``
   启动避免字节码写入）。

数据集刻意覆盖：期内出库、历史入库成本、盘点快照、退货冲减与缺参数
降级（PARAM_MISSING）等多条 M1/M2 代码路径，保证确定性声明对主要
计算分支成立，而非仅对平凡输入成立。
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal

from contracts import (
    AnalysisRequest,
    EngineDataset,
    EventSource,
    MovementRecord,
    SkuRecord,
    SnapshotRecord,
)
from warehouse_engine import WarehouseEngine

REQUEST = AnalysisRequest(
    run_id="run-determinism-0001",
    start_date=date(2026, 6, 1),
    end_date=date(2026, 6, 30),
    warehouse_ids=["WH-01"],
)


def _dataset() -> EngineDataset:
    """稳定数据集：历史入库、期内出库、退货、期末盘点与无成本 SKU。"""
    return EngineDataset(
        skus=[
            SkuRecord(
                sku_id="SKU-0001",
                name="矿泉水 550ml",
                category="饮料",
                unit="瓶",
                unit_cost=Decimal("1.20"),
            ),
            SkuRecord(
                sku_id="SKU-0002",
                name="毛巾（无成本）",
                category="家纺",
                unit="条",
                unit_cost=None,
            ),
        ],
        movements=[
            MovementRecord(
                event_id="EVT-0001",
                sku_id="SKU-0001",
                move_type="INBOUND",
                quantity=Decimal(50),
                move_date=date(2026, 5, 10),
                occurred_at=datetime(2026, 5, 10, 8, 0, tzinfo=UTC),
                warehouse_id="WH-01",
                unit_cost=Decimal("1.20"),
                source=EventSource.IMPORT,
            ),
            MovementRecord(
                event_id="EVT-0002",
                sku_id="SKU-0001",
                move_type="INBOUND",
                quantity=Decimal(30),
                move_date=date(2026, 5, 25),
                occurred_at=datetime(2026, 5, 25, 9, 30, tzinfo=UTC),
                warehouse_id="WH-01",
                unit_cost=Decimal("1.35"),
                source=EventSource.IMPORT,
            ),
            MovementRecord(
                event_id="EVT-0003",
                sku_id="SKU-0001",
                move_type="OUTBOUND",
                quantity=Decimal(12),
                move_date=date(2026, 6, 5),
                occurred_at=datetime(2026, 6, 5, 10, 0, tzinfo=UTC),
                warehouse_id="WH-01",
                source=EventSource.IMPORT,
            ),
            MovementRecord(
                event_id="EVT-0004",
                sku_id="SKU-0001",
                move_type="RETURN",
                quantity=Decimal(3),
                move_date=date(2026, 6, 12),
                occurred_at=datetime(2026, 6, 12, 14, 0, tzinfo=UTC),
                warehouse_id="WH-01",
                source=EventSource.MINI_PROGRAM,
            ),
            MovementRecord(
                event_id="EVT-0005",
                sku_id="SKU-0002",
                move_type="INBOUND",
                quantity=Decimal(8),
                move_date=date(2026, 5, 28),
                occurred_at=datetime(2026, 5, 28, 11, 0, tzinfo=UTC),
                warehouse_id="WH-01",
                source=EventSource.IMPORT,
            ),
        ],
        snapshots=[
            SnapshotRecord(
                sku_id="SKU-0001",
                quantity=Decimal(71),
                snapshot_date=date(2026, 6, 20),
                warehouse_id="WH-01",
            )
        ],
    )


def _analyze_payload() -> str:
    """完整 analyze 结果的规范序列化（含 18 指标与数据质量报告）。"""
    return WarehouseEngine().analyze(REQUEST, _dataset()).model_dump_json()


def test_repeat_run_byte_identical() -> None:
    """同进程连续 3 次 analyze：序列化结果逐字节一致。"""
    payloads = [_analyze_payload() for _ in range(3)]

    assert payloads[0] == payloads[1] == payloads[2]


#: 子进程脚本：从 stdin 读入数据集 JSON，独立 analyze 后输出结果 JSON
#: （以 UTF-8 字节写出，规避 Windows 管道默认编码差异）。
_CHILD_CODE = """
import sys
from datetime import date
from contracts import AnalysisRequest, EngineDataset
from warehouse_engine import WarehouseEngine

dataset = EngineDataset.model_validate_json(sys.stdin.buffer.read().decode("utf-8"))
request = AnalysisRequest(
    run_id="run-determinism-0001",
    start_date=date(2026, 6, 1),
    end_date=date(2026, 6, 30),
    warehouse_ids=["WH-01"],
)
sys.stdout.buffer.write(
    WarehouseEngine().analyze(request, dataset).model_dump_json().encode("utf-8")
)
"""


def test_cross_process_output_stable() -> None:
    """跨进程复跑：子进程输出与父进程逐字节一致（含 dataset_digest）。

    子进程独立解释器启动（不同哈希种子），经契约 JSON 往返重建数据集
    后 analyze；若任何容器迭代序、浮点格式化或时间处理存在进程级
    不确定源，此处当场失败。
    """
    payload_local = _analyze_payload().encode("utf-8")
    dataset_json = _dataset().model_dump_json().encode("utf-8")

    completed = subprocess.run(
        [sys.executable, "-B", "-c", _CHILD_CODE],
        input=dataset_json,
        capture_output=True,
        check=True,
    )

    assert completed.stdout == payload_local
