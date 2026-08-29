"""分析契约：引擎输入与输出的 Pydantic 模型（开发者 B 主导冻结）。

序列化约定（与 docs/开发需求-B引擎Skill.md §4.2 一致）：
- 金额与数量使用 Decimal 真值，JSON 序列化为字符串，禁止 float 作为金额真值；
- 日期使用 YYYY-MM-DD；时间使用 UTC ISO 8601；
- Warning 必须含 code/severity/message/fields/blocking 五要素，UI 依据 code 判断行为；
- 不兼容变更（字段删除、类型或单位改变）必须提升 schema_version。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    model_validator,
)

from contracts.enums import ErrorCode, EventSource, MoveType, WarningSeverity


def _decimal_to_json_str(value: Decimal) -> str:
    """JSON 序列化时将 Decimal 转为字符串，避免浮点精度损失。"""
    return str(value)


#: 金额/数量类型：真值为 Decimal，JSON 输出为字符串（如 "123.45"）
DecimalAmount = Annotated[
    Decimal,
    PlainSerializer(_decimal_to_json_str, return_type=str, when_used="json"),
    WithJsonSchema(
        {
            "type": "string",
            "description": "金额/数量（Decimal 语义），JSON 中以字符串传输，例如 \"123.45\"。",
        }
    ),
]


class AnalysisRequest(BaseModel):
    """分析请求：数据期间、仓库范围、筛选条件与计算参数。"""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "schema_version": "1.0",
                    "run_id": "run-20260829-0001",
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-31",
                    "warehouse_ids": ["WH-01"],
                    "filters": {"categories": ["饮料"]},
                    "parameters": {"service_level": "0.95"},
                }
            ]
        },
    )

    schema_version: str = "1.0"
    run_id: str
    start_date: date
    end_date: date
    warehouse_ids: list[str]
    filters: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_period(self) -> AnalysisRequest:
        """分析期间必须满足 end_date >= start_date。"""
        if self.end_date < self.start_date:
            raise ValueError("end_date 必须大于等于 start_date")
        return self


class SkuRecord(BaseModel):
    """SKU 主数据记录。"""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "sku_id": "SKU-0001",
                    "name": "矿泉水 550ml",
                    "category": "饮料",
                    "unit": "瓶",
                    "unit_cost": "1.20",
                    "currency": "CNY",
                }
            ]
        },
    )

    sku_id: str
    name: str
    category: str
    unit: str
    unit_cost: DecimalAmount | None = Field(default=None, ge=0, description="单位成本")
    currency: str = "CNY"


class MovementRecord(BaseModel):
    """库存流水事件（标准化后）；数量方向由 move_type 决定。"""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "event_id": "EVT-0001",
                    "sku_id": "SKU-0001",
                    "move_type": "OUTBOUND",
                    "quantity": "10",
                    "move_date": "2026-08-05",
                    "occurred_at": "2026-08-05T08:30:00Z",
                    "warehouse_id": "WH-01",
                    "unit_cost": "1.20",
                    "lot_id": "LOT-0001",
                    "source": "IMPORT",
                }
            ]
        },
    )

    event_id: str
    sku_id: str
    move_type: MoveType
    quantity: DecimalAmount = Field(gt=0, description="事件数量（基础单位）")
    move_date: date
    occurred_at: datetime
    warehouse_id: str
    unit_cost: DecimalAmount | None = Field(default=None, ge=0, description="单位成本")
    lot_id: str | None = None
    source: EventSource


class SnapshotRecord(BaseModel):
    """库存快照：时点库存与库存价值。"""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "sku_id": "SKU-0001",
                    "snapshot_date": "2026-08-31",
                    "quantity": "120",
                    "warehouse_id": "WH-01",
                    "inventory_value": "144.00",
                }
            ]
        },
    )

    sku_id: str
    snapshot_date: date
    quantity: DecimalAmount
    warehouse_id: str
    inventory_value: DecimalAmount | None = None


class ReplenishmentRecord(BaseModel):
    """补货参数：按 SKU 提供的需求与成本假设。"""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "sku_id": "SKU-0001",
                    "avg_daily_demand": "3.5",
                    "lead_time_days": "7",
                    "service_level": "0.95",
                    "order_cost": "50.00",
                    "holding_cost": "0.12",
                }
            ]
        },
    )

    sku_id: str
    avg_daily_demand: DecimalAmount = Field(ge=0, description="平均日需求")
    lead_time_days: DecimalAmount = Field(ge=0, description="供应商提前期（天）")
    service_level: DecimalAmount | None = None
    order_cost: DecimalAmount | None = None
    holding_cost: DecimalAmount | None = None


class EngineDataset(BaseModel):
    """标准化数据集：分析周期内的 SKU、流水、快照与补货参数。"""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={"examples": [{"schema_version": "1.0"}]},
    )

    schema_version: str = "1.0"
    skus: list[SkuRecord] = Field(default_factory=list)
    movements: list[MovementRecord] = Field(default_factory=list)
    snapshots: list[SnapshotRecord] = Field(default_factory=list)
    replenishment: list[ReplenishmentRecord] = Field(default_factory=list)


class Warning(BaseModel):
    """结果警告：UI 依据 code 判断行为，中文 message 仅用于展示。"""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "code": "PERIOD_MISMATCH",
                    "severity": "WARN",
                    "message": "move_date 超出分析期间",
                    "fields": ["move_date"],
                    "blocking": False,
                }
            ]
        },
    )

    code: str
    severity: WarningSeverity
    message: str
    fields: list[str] = Field(default_factory=list)
    blocking: bool = False


class ValidationIssue(BaseModel):
    """单条校验问题；row 为记录在其分区内的下标（从 0 开始），无对应记录时为 None。"""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "row": 3,
                    "field": "movements.quantity",
                    "reason": "数量小数位超过上限 3",
                    "code": "DATA_VALIDATION_FAILED",
                }
            ]
        },
    )

    row: int | None = None
    field: str
    reason: str
    code: ErrorCode


class ValidationReport(BaseModel):
    """校验报告：存在阻断 issue 时 valid=False；warnings 为非阻断提示。"""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={"examples": [{"valid": False, "issues": [], "warnings": []}]},
    )

    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)


class ResultMetric(BaseModel):
    """结构化指标：携带公式标识与版本，保证结果可追溯。"""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "name": "inventory_turnover",
                    "value": 4.2,
                    "unit": "次/年",
                    "formula_id": "kpi.inventory_turnover",
                    "formula_version": "0.1.0",
                    "sample_count": 30,
                }
            ]
        },
    )

    name: str
    value: float | int | str
    unit: str
    formula_id: str
    formula_version: str
    sample_count: int


class InputSummary(BaseModel):
    """输入摘要：结果可追溯与历史重开的依据。"""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "sku_count": 12,
                    "movement_count": 340,
                    "snapshot_count": 12,
                    "period_start": "2026-08-01",
                    "period_end": "2026-08-31",
                    "dataset_digest": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
                }
            ]
        },
    )

    sku_count: int
    movement_count: int
    snapshot_count: int
    period_start: date
    period_end: date
    dataset_digest: str


class AnalysisResult(BaseModel):
    """分析结果：携带版本、期间、指标、警告、摘要与输入摘要。"""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "schema_version": "1.0",
                    "run_id": "run-20260829-0001",
                    "engine_version": "0.1.0",
                    "formula_version": "0.1.0",
                    "period_start": "2026-08-01",
                    "period_end": "2026-08-31",
                    "metrics": [],
                    "warnings": [],
                    "summary": "M0 占位结果",
                    "input_summary": {
                        "sku_count": 1,
                        "movement_count": 2,
                        "snapshot_count": 1,
                        "period_start": "2026-08-01",
                        "period_end": "2026-08-31",
                        "dataset_digest": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
                    },
                }
            ]
        },
    )

    schema_version: str = "1.0"
    run_id: str
    engine_version: str
    formula_version: str
    period_start: date
    period_end: date
    metrics: list[ResultMetric] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)
    summary: str
    input_summary: InputSummary


class CapabilityDescriptor(BaseModel):
    """计算能力描述：供工作台展示引擎能力与引用的契约/公式。"""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "name": "kpi",
                    "version": "0.1.0",
                    "description": "库存 KPI 计算",
                    "input_schema_ref": "packages/contracts-schema/analysis-request.schema.json",
                    "output_schema_ref": "packages/contracts-schema/analysis-result.schema.json",
                    "formula_ids": ["kpi.cogs"],
                }
            ]
        },
    )

    name: str
    version: str
    description: str
    input_schema_ref: str
    output_schema_ref: str
    formula_ids: list[str] = Field(default_factory=list)
