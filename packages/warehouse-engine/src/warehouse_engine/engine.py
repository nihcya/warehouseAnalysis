"""WarehouseEngine：M1 引擎（KPI/COGS 真实计算，其余能力维持占位）。

M1 范围：
- validate_dataset：真实基础校验（精度、重复事件、SKU 引用、期间、负库存、
  冲销引用与盘点实盘数量）；
- analyze：先校验（阻断抛 DataValidationError），通过则计算真实 KPI/COGS 指标
  （F-KPI-001~008、F-COGS-001），abc-aging/replenishment/forecasting/benchmark
  四类维持 M0 占位行为与占位 Warning，并随结果生成数据质量报告；
- list_capabilities：五个计算能力描述（公式 ID 冻结于 docs/formula-spec.md）。
"""

from __future__ import annotations

from collections.abc import Callable

from warehouse_engine.__version__ import ENGINE_VERSION, FORMULA_VERSION
from warehouse_engine.calculators import (
    abc_aging,
    benchmark_compare,
    forecasting,
    inventory_kpi,
    replenishment,
)
from warehouse_engine.contracts import (
    AnalysisRequest,
    AnalysisResult,
    CapabilityDescriptor,
    EngineDataset,
    ValidationReport,
    Warning,
    WarningSeverity,
)
from warehouse_engine.errors import DataValidationError
from warehouse_engine.result import build_analysis_result, build_data_quality
from warehouse_engine.validation.rules import apply_dataset_rules

#: 进度回调：接收 0.0~1.0 的完成比例
ProgressCallback = Callable[[float], None]

#: 契约 Schema 引用路径（由 scripts/export_schemas.py 生成并维护）
REQUEST_SCHEMA_REF = "packages/contracts-schema/analysis-request.schema.json"
RESULT_SCHEMA_REF = "packages/contracts-schema/analysis-result.schema.json"


def _capability(
    name: str,
    description: str,
    formula_ids: tuple[str, ...],
) -> CapabilityDescriptor:
    """构建能力描述，统一引用契约 Schema 与公式口径文档。"""
    return CapabilityDescriptor(
        name=name,
        version=ENGINE_VERSION,
        description=f"{description} 公式口径见 docs/formula-spec.md。",
        input_schema_ref=REQUEST_SCHEMA_REF,
        output_schema_ref=RESULT_SCHEMA_REF,
        formula_ids=list(formula_ids),
    )


class WarehouseEngine:
    """仓库品类分析引擎（M1：KPI/COGS 真实计算）。"""

    engine_version: str = ENGINE_VERSION
    formula_version: str = FORMULA_VERSION

    def validate_dataset(
        self,
        request: AnalysisRequest,
        dataset: EngineDataset,
    ) -> ValidationReport:
        """执行数据集基础校验，返回结构化报告；存在阻断 issue 时 valid=False。"""
        issues, warnings = apply_dataset_rules(dataset, request)
        return ValidationReport(valid=not issues, issues=issues, warnings=warnings)

    def analyze(
        self,
        request: AnalysisRequest,
        dataset: EngineDataset,
        progress: ProgressCallback | None = None,
    ) -> AnalysisResult:
        """M1 行为：先校验；阻断时抛 DataValidationError，否则返回真实 KPI/COGS 结果。

        - 不修改传入的 request 与 dataset；
        - KPI/COGS（F-KPI-001~008、F-COGS-001）返回真实计算结果；
        - abc-aging/replenishment/forecasting/benchmark 四类维持 M0 占位行为，
          以 code=ANALYSIS_PLACEHOLDER 的非阻断 Warning 标注；
        - warnings 汇总校验层与计算层全部非阻断警告，data_quality 按码汇总
          （计数 + 明细）随结果返回；
        - 同一输入重复调用，序列化结果逐字节一致（无随机性、无时间依赖）；
        - progress 回调按阶段推进：0.0（校验开始）→ 0.3（校验完成）→
          0.9（KPI 计算完成）→ 1.0（结果组装完成）。
        """
        if progress is not None:
            progress(0.0)
        report = self.validate_dataset(request, dataset)
        if not report.valid:
            raise DataValidationError(
                "输入数据校验未通过，分析已阻断。",
                details=[issue.model_dump(mode="json") for issue in report.issues],
            )
        if progress is not None:
            progress(0.3)
        kpi = inventory_kpi.calculate(request, dataset)
        if progress is not None:
            progress(0.9)
        placeholder = Warning(
            code="ANALYSIS_PLACEHOLDER",
            severity=WarningSeverity.INFO,
            message=(
                "abc-aging/replenishment/forecasting/benchmark 四类计算器尚未实现，"
                "相应结果为占位（本次已返回 KPI/COGS 真实指标）。"
            ),
            fields=[],
            blocking=False,
        )
        warnings = [*report.warnings, *kpi.warnings, placeholder]
        result = build_analysis_result(
            request,
            dataset,
            engine_version=self.engine_version,
            formula_version=self.formula_version,
            metrics=kpi.metrics,
            warnings=warnings,
            summary=(
                "KPI/COGS 真实结果（F-KPI-001~008、F-COGS-001）；"
                "abc-aging/replenishment/forecasting/benchmark 为 M0 占位，待 M1/M2 交付。"
            ),
            data_quality=build_data_quality(warnings),
        )
        if progress is not None:
            progress(1.0)
        return result

    def list_capabilities(self) -> list[CapabilityDescriptor]:
        """返回五个计算能力的占位描述。"""
        return [
            _capability(
                name="kpi",
                description=(
                    "库存 KPI：期初库存（opening_qty）、期末库存（closing_qty）、出库量（out_qty）、"
                    "动销率（active_ratio）、库存价值（inventory_value）、周转率（turnover）、"
                    "周转天数（turnover_days）、覆盖天数（coverage_days）与 COGS。"
                ),
                formula_ids=inventory_kpi.FORMULA_IDS,
            ),
            _capability(
                name="abc-aging",
                description="ABC 分层（classification）、库龄分布（inventory_age，左闭右开区间）与呆滞识别（stale）。",
                formula_ids=abc_aging.FORMULA_IDS,
            ),
            _capability(
                name="replenishment",
                description="补货建议：安全库存（safety_stock）、补货点（reorder_point）与建议补货量（suggested_qty）。",
                formula_ids=replenishment.FORMULA_IDS,
            ),
            _capability(
                name="forecasting",
                description="需求预测与误差评估（4 周移动平均基线，MAPE/WAPE/MAE/RMSE）。",
                formula_ids=forecasting.FORMULA_IDS,
            ),
            _capability(
                name="benchmark",
                description="行业基准比较；无匹配基准时返回 BENCHMARK_UNAVAILABLE。",
                formula_ids=benchmark_compare.FORMULA_IDS,
            ),
        ]
