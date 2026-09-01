#!/usr/bin/env python3
"""生成符合 warehouseAnalysis 数据标准的 mock 数据（确定性、可复现）。

产出两份 CSV，便于在工作台「导入」向导里先导入主数据、再导入事件：

- ``mock.csv``        : 1000 条库存事件（inventory_events CSV 标准）
- ``mock_master.csv`` : 被引用 SKU 的主数据（master_data CSV 标准）

本文件只负责「造数据」，不提交任何 CSV；真正的 fixture 由本脚本在本地生成，
从而让端到端验证（见同目录 ``run_mock_test.py``）可脱离仓库内置样例数据独立运行。

数据标准来源：
  apps/workbench-desktop/app/application/import_manager.py
    EVENT_REQUIRED_FIELDS  = (event_id, sku_id, warehouse_id, move_type, quantity, occurred_at)
    EVENT_OPTIONAL_FIELDS  = (unit_cost, source_ref)
    MASTER_REQUIRED_FIELDS = (sku_id, name)
    MASTER_OPTIONAL_FIELDS = (category, sub_category, unit, unit_cost, industry)
  local_data.models.MOVE_TYPES (8 值)
  docs/data-dictionary.md §1.11（quantity 恒为正、scale<=3；unit_cost scale<=2；occurred_at YYYY-MM-DD）

用法：
    python gen_mock_data.py --out /path/to/dir
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

SEED = 20260830  # 固定种子 => 可复现
MOVE_TYPES = [
    "INBOUND",
    "OUTBOUND",
    "RETURN",
    "SCRAP",
    "ADJUSTMENT",
    "TRANSFER_IN",
    "TRANSFER_OUT",
]
# 注：REVERSAL 需要 reversal_of 关联，本生成器不产出，避免导入错误。

# 20 个 SKU 主数据：(id, name, category, sub_category, unit, unit_cost(str), industry)
SKUS = [
    ("SKU-001", "苏打水 330ml", "饮料", "碳酸饮料", "瓶", "2.50", "快消"),
    ("SKU-002", "纯棉T恤 男 M", "服饰", "上装", "件", "19.90", "零售"),
    ("SKU-003", "抽纸 3层 100抽", "日用品", "纸品", "包", "3.20", "快消"),
    ("SKU-004", "蓝牙耳机 无线", "电子", "音频", "副", "89.00", "电子"),
    ("SKU-005", "大米 5kg", "食品", "粮油", "袋", "32.80", "快消"),
    ("SKU-006", "洗发水 500ml", "日化", "洗护", "瓶", "25.50", "快消"),
    ("SKU-007", "运动鞋 男 42", "服饰", "鞋靴", "双", "129.00", "零售"),
    ("SKU-008", "矿泉水 1.5L", "饮料", "饮用水", "瓶", "1.80", "快消"),
    ("SKU-009", "笔记本 A5", "文具", "本册", "本", "5.50", "办公"),
    ("SKU-010", "充电宝 20000mAh", "电子", "电源", "个", "75.00", "电子"),
    ("SKU-011", "巧克力 礼盒", "食品", "零食", "盒", "45.00", "快消"),
    ("SKU-012", "不锈钢保温杯 500ml", "日用品", "杯具", "个", "39.90", "零售"),
    ("SKU-013", "婴儿纸尿裤 L", "日用品", "母婴", "包", "56.00", "快消"),
    ("SKU-014", "机械键盘 87键", "电子", "外设", "个", "199.00", "电子"),
    ("SKU-015", "橄榄油 1L", "食品", "粮油", "瓶", "68.00", "快消"),
    ("SKU-016", "毛巾 纯棉", "日用品", "浴用", "条", "9.90", "零售"),
    ("SKU-017", "速溶咖啡 200g", "食品", "冲饮", "罐", "38.00", "快消"),
    ("SKU-018", "数据线 Type-C", "电子", "配件", "根", "12.00", "电子"),
    ("SKU-019", "洗衣液 2L", "日化", "洗护", "瓶", "22.00", "快消"),
    ("SKU-020", "抱枕 沙发", "日用品", "家居", "个", "29.90", "零售"),
]
SKU_COST = {s[0]: Decimal(s[5]) for s in SKUS}
WAREHOUSES = ["WH-01", "WH-02", "WH-03"]

N_EVENTS = 1000
START = date(2026, 3, 1)
SPAN_DAYS = 181  # 到 2026-08-29 止


def fmt_qty(q: Decimal) -> str:
    if q == q.to_integral_value():
        return str(int(q))
    return str(q)


def make_source_ref(move_type: str) -> str:
    import random

    n = random.randint(100000, 999999)
    if move_type in ("INBOUND", "TRANSFER_IN"):
        return f"PO-{n}"
    if move_type in ("OUTBOUND", "TRANSFER_OUT"):
        return f"SO-{n}"
    if move_type == "RETURN":
        return f"RMA-{n}"
    if move_type == "SCRAP":
        return f"SCR-{n}"
    return f"ADJ-{n}"


def gen_events(rng: random.Random):
    rows = []
    combos = [(SKUS[i // 3][0], WAREHOUSES[i % 3]) for i in range(60)]
    for idx in range(N_EVENTS):
        combo = combos[idx % 60]
        order = idx // 60
        sku_id, wh = combo
        if order == 0:
            move_type = "INBOUND"
        else:
            move_type = rng.choices(
                MOVE_TYPES, weights=[28, 42, 5, 5, 5, 5, 5], k=1
            )[0]
        base = rng.randint(1, 600)
        q = Decimal(base) + (Decimal("0.5") if rng.random() < 0.3 else Decimal(0))
        d = START + timedelta(days=rng.randint(0, SPAN_DAYS))
        occurred = d.strftime("%Y-%m-%d")
        unit_cost = SKU_COST[sku_id]
        rows.append(
            {
                "event_id": f"EVT-{idx + 1:06d}",
                "sku_id": sku_id,
                "warehouse_id": wh,
                "move_type": move_type,
                "quantity": fmt_qty(q),
                "occurred_at": occurred,
                "unit_cost": str(unit_cost),
                "source_ref": make_source_ref(move_type),
            }
        )
    return rows


def gen_master():
    return [
        {
            "sku_id": s[0],
            "name": s[1],
            "category": s[2],
            "sub_category": s[3],
            "unit": s[4],
            "unit_cost": s[5],
            "industry": s[6],
        }
        for s in SKUS
    ]


def write_csv(path: Path, fieldnames, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"written: {path}  ({len(rows)} data rows)")


def validate_events(path: Path):
    from collections import Counter

    req = ["event_id", "sku_id", "warehouse_id", "move_type", "quantity", "occurred_at"]
    opt = ["unit_cost", "source_ref"]
    with path.open(encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        assert r.fieldnames == req + opt, f"header mismatch: {r.fieldnames}"
        rows = list(r)
    errors = []
    seen = set()
    for i, row in enumerate(rows, start=2):
        eid = row["event_id"]
        if eid in seen:
            errors.append(f"row{i}: dup event_id {eid}")
        seen.add(eid)
        for k in req:
            if not (row[k] or "").strip():
                errors.append(f"row{i}: required empty {k}")
        mt = (row["move_type"] or "").strip()
        if mt not in MOVE_TYPES:
            errors.append(f"row{i}: bad move_type {mt!r}")
        try:
            q = Decimal(row["quantity"])
        except InvalidOperation:
            errors.append(f"row{i}: quantity not decimal {row['quantity']!r}")
        else:
            if not q.is_finite() or q <= 0:
                errors.append(f"row{i}: quantity<=0 {q}")
            elif q.as_tuple().exponent < -3:
                errors.append(f"row{i}: quantity scale>3 {q}")
        ds = (row["occurred_at"] or "").strip()
        try:
            pd = date.fromisoformat(ds)
            if pd.isoformat() != ds:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(f"row{i}: bad date {ds!r}")
        uc = (row["unit_cost"] or "").strip()
        if uc:
            try:
                c = Decimal(uc)
            except InvalidOperation:
                errors.append(f"row{i}: unit_cost not decimal {uc!r}")
            else:
                if not c.is_finite() or c < 0:
                    errors.append(f"row{i}: unit_cost<0 {c}")
                elif c.as_tuple().exponent < -2:
                    errors.append(f"row{i}: unit_cost scale>2 {c}")
    print(f"validate events: rows={len(rows)} errors={len(errors)}")
    if errors[:5]:
        print("  sample errors:", errors[:5])
    mt_counter = Counter(r["move_type"] for r in rows)
    print("  move_type分布:", dict(mt_counter))
    sku_set = {r["sku_id"] for r in rows}
    wh_set = {r["warehouse_id"] for r in rows}
    print(f"  distinct sku={len(sku_set)} warehouse={wh_set}")
    assert not errors, "event validation failed"
    return sku_set, wh_set


def validate_master(path: Path):
    req = ["sku_id", "name"]
    opt = ["category", "sub_category", "unit", "unit_cost", "industry"]
    with path.open(encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        assert r.fieldnames == req + opt, f"master header mismatch: {r.fieldnames}"
        rows = list(r)
    errs = []
    seen = set()
    for i, row in enumerate(rows, start=2):
        for k in req:
            if not (row[k] or "").strip():
                errs.append(f"row{i}: required empty {k}")
        sid = row["sku_id"]
        if sid in seen:
            errs.append(f"row{i}: dup sku_id {sid}")
        seen.add(sid)
        uc = (row["unit_cost"] or "").strip()
        if uc:
            try:
                c = Decimal(uc)
            except InvalidOperation:
                errs.append(f"row{i}: unit_cost not decimal")
            else:
                if not c.is_finite() or c < 0 or c.as_tuple().exponent < -2:
                    errs.append(f"row{i}: unit_cost invalid {uc}")
    print(f"validate master: rows={len(rows)} errors={len(errs)}")
    assert not errs, "master validation failed"
    return {r["sku_id"] for r in rows}


def generate(out_dir: Path) -> tuple[Path, Path]:
    """生成两份 CSV 到 out_dir，返回 (events_csv, master_csv)。"""
    import random

    rng = random.Random(SEED)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ev_path = out_dir / "mock.csv"
    m_path = out_dir / "mock_master.csv"
    # 局部 import random 已在 gen_events 内引用；这里直接调用
    events = gen_events(rng)
    master = gen_master()
    write_csv(
        ev_path,
        ["event_id", "sku_id", "warehouse_id", "move_type", "quantity", "occurred_at", "unit_cost", "source_ref"],
        events,
    )
    write_csv(
        m_path,
        ["sku_id", "name", "category", "sub_category", "unit", "unit_cost", "industry"],
        master,
    )
    ev_skus, ev_wh = validate_events(ev_path)
    m_skus = validate_master(m_path)
    missing = ev_skus - m_skus
    print("events 引用的 SKU 是否都在主数据中:", "是" if not missing else f"否(缺失 {missing})")
    print("仓库(需在主数据/界面中建立):", sorted(ev_wh))
    print("OK: 两份文件均符合数据标准。")
    return ev_path, m_path


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 warehouseAnalysis mock 数据")
    ap.add_argument("--out", default=".", help="输出目录（默认当前目录）")
    args = ap.parse_args()
    generate(Path(args.out))


if __name__ == "__main__":
    main()
