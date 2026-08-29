"""数据集级共享校验规则。

由 ``WarehouseEngine.validate_dataset``（传入 request，含期间规则）与
``warehouse_engine.validation.validate_raw_dataset``（无 request，跳过期间规则）复用。
口径与 docs/formula-spec.md 的精度冻结保持一致：
数量 scale<=3、金额 scale<=2；冲销引用与盘点实盘数量校验见 §2.5/§2.6。
"""

from __future__ import annotations

from decimal import Decimal

from warehouse_engine.contracts import (
    AnalysisRequest,
    EngineDataset,
    ErrorCode,
    MovementRecord,
    MoveType,
    ValidationIssue,
    Warning,
    WarningSeverity,
)

#: 数量类字段允许的最大小数位（scale）
QUANTITY_MAX_SCALE = 3

#: 金额类字段允许的最大小数位（scale）
MONEY_MAX_SCALE = 2


def _scale_of(value: Decimal) -> int:
    """返回 Decimal 的小数位数（scale）。"""
    exponent = value.as_tuple().exponent
    # NaN/Infinity 的 exponent 为字符串标记（'n'/'N'/'F'），按无小数位处理
    if not isinstance(exponent, int):
        return 0
    return -exponent if exponent < 0 else 0


def _precision_issue(
    section: str,
    row: int,
    field: str,
    value: Decimal,
    max_scale: int,
) -> ValidationIssue | None:
    """小数位超限时生成阻断 issue，否则返回 None。"""
    scale = _scale_of(value)
    if scale <= max_scale:
        return None
    return ValidationIssue(
        row=row,
        field=f"{section}.{field}",
        reason=f"{section}[{row}].{field} 小数位为 {scale}，超过允许上限 {max_scale}",
        code=ErrorCode.DATA_VALIDATION_FAILED,
    )


def _check_precision(dataset: EngineDataset) -> list[ValidationIssue]:
    """数量 precision：数量 scale<=3、金额 scale<=2（违规发阻断 issue）。"""
    issues: list[ValidationIssue] = []

    def check(section: str, row: int, field: str, value: Decimal | None, max_scale: int) -> None:
        if value is None:
            return
        issue = _precision_issue(section, row, field, value, max_scale)
        if issue is not None:
            issues.append(issue)

    for row, sku in enumerate(dataset.skus):
        check("skus", row, "unit_cost", sku.unit_cost, MONEY_MAX_SCALE)
    for row, movement in enumerate(dataset.movements):
        check("movements", row, "quantity", movement.quantity, QUANTITY_MAX_SCALE)
        check("movements", row, "unit_cost", movement.unit_cost, MONEY_MAX_SCALE)
    for row, snapshot in enumerate(dataset.snapshots):
        check("snapshots", row, "quantity", snapshot.quantity, QUANTITY_MAX_SCALE)
        check("snapshots", row, "inventory_value", snapshot.inventory_value, MONEY_MAX_SCALE)
    for row, record in enumerate(dataset.replenishment):
        check("replenishment", row, "avg_daily_demand", record.avg_daily_demand, QUANTITY_MAX_SCALE)
        check("replenishment", row, "lead_time_days", record.lead_time_days, QUANTITY_MAX_SCALE)
        check("replenishment", row, "order_cost", record.order_cost, MONEY_MAX_SCALE)
        check("replenishment", row, "holding_cost", record.holding_cost, MONEY_MAX_SCALE)
    return issues


def _check_duplicate_events(dataset: EngineDataset) -> list[ValidationIssue]:
    """重复 event_id 检查：同一事件 ID 出现多次时，对后续重复项发 DUPLICATE_EVENT issue。"""
    issues: list[ValidationIssue] = []
    first_seen: dict[str, int] = {}
    for row, movement in enumerate(dataset.movements):
        first_row = first_seen.get(movement.event_id)
        if first_row is None:
            first_seen[movement.event_id] = row
            continue
        issues.append(
            ValidationIssue(
                row=row,
                field="movements.event_id",
                reason=f"movements[{row}] 事件 ID 与第 {first_row} 行重复：{movement.event_id}",
                code=ErrorCode.DUPLICATE_EVENT,
            )
        )
    return issues


def _check_sku_references(dataset: EngineDataset) -> list[ValidationIssue]:
    """流水引用了数据集中不存在的 SKU 时发 SKU_NOT_FOUND issue。"""
    known_sku_ids = {sku.sku_id for sku in dataset.skus}
    issues: list[ValidationIssue] = []
    for row, movement in enumerate(dataset.movements):
        if movement.sku_id not in known_sku_ids:
            issues.append(
                ValidationIssue(
                    row=row,
                    field="movements.sku_id",
                    reason=f"movements[{row}] 引用了不存在的 SKU：{movement.sku_id}",
                    code=ErrorCode.SKU_NOT_FOUND,
                )
            )
    return issues


def _check_period(request: AnalysisRequest, dataset: EngineDataset) -> list[Warning]:
    """move_date 超出请求期间时发非阻断 PERIOD_MISMATCH Warning。

    期间口径与 docs/formula-spec.md §2.1 一致：左闭右开 ``[start_date, end_date)``，
    即 ``move_date == end_date`` 不计入本期，同样触发 PERIOD_MISMATCH。
    """
    warnings: list[Warning] = []
    for row, movement in enumerate(dataset.movements):
        if movement.move_date < request.start_date or movement.move_date >= request.end_date:
            warnings.append(
                Warning(
                    code="PERIOD_MISMATCH",
                    severity=WarningSeverity.WARN,
                    message=(
                        f"movements[{row}] 的 move_date={movement.move_date.isoformat()} "
                        f"超出分析期间 [{request.start_date}, {request.end_date})。"
                    ),
                    fields=["move_date"],
                    blocking=False,
                )
            )
    return warnings


def _check_negative_snapshots(dataset: EngineDataset) -> list[Warning]:
    """负库存快照发非阻断 NEGATIVE_BALANCE Warning。"""
    warnings: list[Warning] = []
    for row, snapshot in enumerate(dataset.snapshots):
        if snapshot.quantity < 0:
            warnings.append(
                Warning(
                    code="NEGATIVE_BALANCE",
                    severity=WarningSeverity.WARN,
                    message=(
                        f"snapshots[{row}] 的 quantity 为负（{snapshot.quantity}），"
                        "请核对库存事件完整性。"
                    ),
                    fields=["quantity"],
                    blocking=False,
                )
            )
    return warnings


def _check_reversal_references(dataset: EngineDataset) -> list[ValidationIssue]:
    """冲销引用校验（formula-spec §2.6）。

    - REVERSAL 事件缺少 ``reversal_of``、或引用的 ``event_id`` 不存在：
      阻断 issue（code=DATA_VALIDATION_FAILED），定位到该冲销事件；
    - 冲销引用链出现环（含自引用）：属结构/引用错误，阻断。
    """
    issues: list[ValidationIssue] = []
    events_by_id: dict[str, MovementRecord] = {
        movement.event_id: movement for movement in dataset.movements
    }
    for row, movement in enumerate(dataset.movements):
        if movement.move_type is not MoveType.REVERSAL:
            continue
        reference = movement.reversal_of
        if not reference:
            issues.append(
                ValidationIssue(
                    row=row,
                    field="movements.reversal_of",
                    reason=(
                        f"movements[{row}] 冲销事件 {movement.event_id} "
                        "缺少被冲销事件引用（reversal_of 为空）。"
                    ),
                    code=ErrorCode.DATA_VALIDATION_FAILED,
                )
            )
            continue
        target = events_by_id.get(reference)
        if target is None:
            issues.append(
                ValidationIssue(
                    row=row,
                    field="movements.reversal_of",
                    reason=(
                        f"movements[{row}] 冲销事件 {movement.event_id} 引用的 "
                        f"event_id={reference} 在数据集中不存在。"
                    ),
                    code=ErrorCode.DATA_VALIDATION_FAILED,
                )
            )
            continue
        # 沿引用链检测环：自引用或回到已访问节点均视为结构错误
        visited = {movement.event_id}
        current = target
        while current.move_type is MoveType.REVERSAL:
            next_id = current.reversal_of
            if next_id is None or next_id not in events_by_id:
                break  # 引用缺失已由上方或对应该行的检查覆盖
            if next_id in visited:
                issues.append(
                    ValidationIssue(
                        row=row,
                        field="movements.reversal_of",
                        reason=(
                            f"movements[{row}] 冲销事件 {movement.event_id} 的 "
                            f"引用链存在环（经过 {next_id}）。"
                        ),
                        code=ErrorCode.DATA_VALIDATION_FAILED,
                    )
                )
                break
            visited.add(next_id)
            current = events_by_id[next_id]
    return issues


def _check_stocktake_on_hand(dataset: EngineDataset) -> list[ValidationIssue]:
    """盘点实盘数量（on_hand_qty）合法性（formula-spec §2.5）。

    STOCKTAKE 事件的 quantity 即实盘数量，必须为正数（盘点记录不携带方向）；
    非法值阻断（code=DATA_VALIDATION_FAILED）。契约层 ``quantity > 0`` 约束
    在记录解析时已拦截同类问题，本规则保证重放内核的替换值恒为正。
    """
    issues: list[ValidationIssue] = []
    for row, movement in enumerate(dataset.movements):
        if movement.move_type is not MoveType.STOCKTAKE:
            continue
        if movement.quantity <= 0:
            issues.append(
                ValidationIssue(
                    row=row,
                    field="movements.quantity",
                    reason=(
                        f"movements[{row}] 盘点事件 {movement.event_id} 的实盘数量 "
                        f"on_hand_qty={movement.quantity} 非正数，无法作为余额替换值。"
                    ),
                    code=ErrorCode.DATA_VALIDATION_FAILED,
                )
            )
    return issues


def apply_dataset_rules(
    dataset: EngineDataset,
    request: AnalysisRequest | None = None,
) -> tuple[list[ValidationIssue], list[Warning]]:
    """按固定顺序执行全部数据集级规则，返回（阻断 issue 列表, 非阻断 Warning 列表）。

    request 为 None 时跳过需要请求期间的规则（供原始载荷入口使用）。
    """
    issues: list[ValidationIssue] = []
    issues.extend(_check_precision(dataset))
    issues.extend(_check_duplicate_events(dataset))
    issues.extend(_check_sku_references(dataset))
    issues.extend(_check_reversal_references(dataset))
    issues.extend(_check_stocktake_on_hand(dataset))

    warnings: list[Warning] = []
    warnings.extend(_check_negative_snapshots(dataset))
    if request is not None:
        warnings.extend(_check_period(request, dataset))
    return issues, warnings
