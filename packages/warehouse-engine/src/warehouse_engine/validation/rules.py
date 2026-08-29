"""数据集级共享校验规则。

由 ``WarehouseEngine.validate_dataset``（传入 request，含期间规则）与
``warehouse_engine.validation.validate_raw_dataset``（无 request，跳过期间规则）复用。
口径与 docs/formula-spec.md 的精度冻结保持一致：
数量 scale<=3、金额 scale<=2。
"""

from __future__ import annotations

from decimal import Decimal

from warehouse_engine.contracts import (
    AnalysisRequest,
    EngineDataset,
    ErrorCode,
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

    warnings: list[Warning] = []
    warnings.extend(_check_negative_snapshots(dataset))
    if request is not None:
        warnings.extend(_check_period(request, dataset))
    return issues, warnings
