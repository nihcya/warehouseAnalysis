"""WarehouseEngine：M2 引擎（五类分析能力全部真实计算）。

M2 范围：
- validate_dataset：真实基础校验（精度、重复事件、SKU 引用、期间、负库存、
  冲销引用与盘点实盘数量）；
- analyze：先校验（阻断抛 DataValidationError），通过则计算五类公式共 18 个
  指标（F-KPI-001~008 与 F-COGS-001、F-ABC-001 / F-AGE-001 / F-STALE-001、
  F-REPL-001~003、F-FCST-001~002、F-BM-001），并随结果生成数据质量报告；
  ``ANALYSIS_PLACEHOLDER`` 占位 Warning 已随四类计算器实装而移除；
- list_capabilities：五个计算能力描述（公式 ID 冻结于 docs/formula-spec.md）。

执行效率：重放内核与 KPI 各只执行一次，结果以参数形式注入依赖它们的计算器
（abc_aging / replenishment / forecasting / benchmark_compare）。
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
)
from warehouse_engine.errors import DataValidationError
from warehouse_engine.replay import replay_movements
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
    """仓库品类分析引擎（M2：五类分析能力全部真实计算）。"""

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
        """M2 行为：先校验；阻断时抛 DataValidationError，否则返回五类公式全部结果。

        - 不修改传入的 request 与 dataset；
        - 输出 18 个指标：KPI/COGS（F-KPI-001~008、F-COGS-001）、ABC/库龄/
          呆滞（F-ABC-001、F-AGE-001、F-STALE-001）、补货（F-REPL-001~003）、
          预测与误差（F-FCST-001~002）、行业基准（F-BM-001），每个 formula_id
          恰对应一个数据集级聚合指标；
        - 重放内核与 KPI 各执行一次，结果注入依赖它们的四个计算器；
        - warnings 按固定顺序拼接：校验层 → KPI → abc-aging → 补货 → 预测 →
          基准；data_quality 按码汇总（计数 + 明细）随结果返回；
        - 同一输入重复调用，序列化结果逐字节一致（无随机性、无时间依赖）；
        - progress 回调按阶段推进：0.0（校验开始）→ 0.3（校验完成）→
          0.5（KPI 完成）→ 0.7（ABC/库龄/呆滞完成）→ 0.85（补货/预测/基准
          完成）→ 1.0（结果组装完成）。
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
        outcome = replay_movements(request, dataset.movements)
        kpi = inventory_kpi.calculate(request, dataset, outcome=outcome)
        if progress is not None:
            progress(0.5)
        abc = abc_aging.calculate(request, dataset, kpi=kpi, outcome=outcome)
        if progress is not None:
            progress(0.7)
        repl = replenishment.calculate(request, dataset, kpi=kpi, outcome=outcome)
        fcst = forecasting.calculate(request, dataset, outcome=outcome)
        bench = benchmark_compare.calculate(request, dataset, kpi_metrics=kpi.metrics)
        if progress is not None:
            progress(0.85)
        warnings = [
            *report.warnings,
            *kpi.warnings,
            *abc.warnings,
            *repl.warnings,
            *fcst.warnings,
            *bench.warnings,
        ]
        result = build_analysis_result(
            request,
            dataset,
            engine_version=self.engine_version,
            formula_version=self.formula_version,
            metrics=[*kpi.metrics, *abc.metrics, *repl.metrics, *fcst.metrics, *bench.metrics],
            warnings=warnings,
            summary=(
                "KPI/COGS（F-KPI-001~008、F-COGS-001）、ABC/库龄/呆滞"
                "（F-ABC-001、F-AGE-001、F-STALE-001）、补货（F-REPL-001~003）、"
                "预测与误差（F-FCST-001~002）、行业基准（F-BM-001）共 18 项真实结果。"
            ),
            data_quality=build_data_quality(warnings),
        )
        if progress is not None:
            progress(1.0)
        return result

    def list_capabilities(self) -> list[CapabilityDescriptor]:
        """返回五个计算能力的描述（公式 ID 冻结于 docs/formula-spec.md）。"""
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
