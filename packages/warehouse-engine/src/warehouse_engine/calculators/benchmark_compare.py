"""行业基准比较计算器（F-BM-001，formula-spec §9）。

实现要点：

- **基准来源**：契约 ``EngineDataset`` 无基准表，基准数据由调用方经
  ``request.parameters["benchmarks"]`` 注入（list[dict]）。本计算器**不读文件、
  不访问网络**，符合 B 侧"不读用户文件、不访问网络"的边界约束；版本化基准
  数据集随 fixture 维护（``tests/fixtures/benchmarks/<version>.json``），由调用方
  载入后注入；
- **必备字段（§9）**：``source`` / ``region`` / ``industry`` / ``sample_scope`` /
  ``updated_at`` / ``benchmark_version`` / ``unit`` / ``applicability`` 为基准
  记录必备八字段，另需 ``metric``（指标名）与 ``value``（基准值）。**任一字段
  缺失即丢弃该记录**，不参与匹配——绝不使用无来源说明的经验数字兜底；
- **匹配规则（§9）**：按 ``(industry, region, metric)`` 匹配；请求侧 ``region``
  缺省取"全国"口径；多条命中取 ``benchmark_version`` 与 ``updated_at`` 最新者，
  两者并列时按 ``source`` 字典序取首项（确定性）；
- **无匹配（§9）**：发非阻断 ``BENCHMARK_UNAVAILABLE``，**不输出任何比较
  结论**，指标输出 ``"null"`` 并标注 ``reason=benchmark_unavailable``；
- **聚合口径**：``BM.DEVIATION_RATIO`` 为各比较项**相对偏差的算术平均**——
  各指标单位不同（CNY / 件 / 比率 / 天数），跨单位求和无意义，故取单位无关的
  相对偏差均值（M2 实现决策，已在 SKILL.md 与 m2-handover-b 注明）；
- **输出粒度**：仅聚合进 ``AnalysisResult.metrics``（每个 formula_id 恰一个
  指标），逐项比较结果与基准完整元数据留在 :class:`BenchmarkResult` 内供测试
  断言，不新增契约字段；
- **确定性**：指标按名称字典序、命中记录按 source 字典序，版本号按数字分量
  解析比较，全链路 Decimal（无 float）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from warehouse_engine.calculators._common import (
    NULL_VALUE,
    build_metric,
    round_ratio,
)
from warehouse_engine.contracts import (
    AnalysisRequest,
    EngineDataset,
    ResultMetric,
    Warning,
    WarningSeverity,
)

#: 本模块负责的公式 ID（基准比较口径，编号冻结于 docs/formula-spec.md）
FORMULA_IDS: tuple[str, ...] = ("F-BM-001",)  # 行业基准比较 benchmark_compare

#: 输出指标名（AnalysisResult.metrics[].name 的稳定标识）
METRIC_NAMES: dict[str, str] = {"F-BM-001": "BM.DEVIATION_RATIO"}

#: 基准记录必备字段（§9 八字段 + 指标名 + 基准值）
REQUIRED_FIELDS: tuple[str, ...] = (
    "source",
    "region",
    "industry",
    "sample_scope",
    "updated_at",
    "benchmark_version",
    "unit",
    "applicability",
    "metric",
    "value",
)

#: 请求侧 region 缺省口径（§9"region 缺省取全国口径"）
DEFAULT_REGION = "全国"


@dataclass(frozen=True)
class BenchmarkRecord:
    """一条可用的行业基准记录（必备八字段 + 指标名与基准值）。"""

    source: str
    region: str
    industry: str
    sample_scope: str
    updated_at: date
    benchmark_version: str
    unit: str
    applicability: str
    #: 指标名（对应 ResultMetric.name）
    metric: str
    value: Decimal


@dataclass(frozen=True)
class BenchmarkComparison:
    """单个指标的比较结果（含基准完整元数据，§9 输出要求）。"""

    metric_name: str
    merchant_value: Decimal
    benchmark_value: Decimal
    #: 绝对差异 = 商户值 − 基准值
    absolute_diff: Decimal
    #: 相对差异 = (商户值 − 基准值) / 基准值；基准值为 0 时为 None
    relative_diff: Decimal | None
    benchmark: BenchmarkRecord


@dataclass(frozen=True)
class BenchmarkResult:
    """基准比较输出：聚合指标、Warning 与逐项比较明细。"""

    metrics: tuple[ResultMetric, ...]
    warnings: tuple[Warning, ...]
    comparisons: tuple[BenchmarkComparison, ...]
    #: 因缺失必备字段被丢弃的基准记录条数（不参与匹配）
    discarded_records: int


def _parse_date(value: Any) -> date | None:
    """解析基准记录的 updated_at（date / datetime / ISO 字符串）。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _version_key(version: str) -> tuple[int, ...]:
    """把版本号解析为可比较的整数元组（非数字分量按 0 处理，确定性）。"""
    parts: list[int] = []
    for part in str(version).split("."):
        digits = "".join(char for char in part if char.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def parse_benchmarks(raw: Any) -> tuple[tuple[BenchmarkRecord, ...], int]:
    """解析并校验基准记录，返回 (可用记录, 被丢弃条数)。

    必备字段任一缺失、为空或 ``value`` / ``updated_at`` 无法解析时丢弃该记录
    （§9：不得使用）。
    """
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return (), 0
    records: list[BenchmarkRecord] = []
    discarded = 0
    for item in raw:
        if not isinstance(item, dict):
            discarded += 1
            continue
        if any(item.get(field) in (None, "") for field in REQUIRED_FIELDS):
            discarded += 1
            continue
        updated_at = _parse_date(item["updated_at"])
        if updated_at is None:
            discarded += 1
            continue
        try:
            value = Decimal(str(item["value"]))
        except InvalidOperation:
            discarded += 1
            continue
        records.append(
            BenchmarkRecord(
                source=str(item["source"]),
                region=str(item["region"]),
                industry=str(item["industry"]),
                sample_scope=str(item["sample_scope"]),
                updated_at=updated_at,
                benchmark_version=str(item["benchmark_version"]),
                unit=str(item["unit"]),
                applicability=str(item["applicability"]),
                metric=str(item["metric"]),
                value=value,
            )
        )
    return tuple(records), discarded


def _select_latest(candidates: list[BenchmarkRecord]) -> BenchmarkRecord:
    """多条命中取 benchmark_version 与 updated_at 最新者；并列按 source 字典序。"""
    ordered = sorted(candidates, key=lambda record: record.source)
    return max(
        ordered, key=lambda record: (_version_key(record.benchmark_version), record.updated_at)
    )


def _merchant_value(metric: ResultMetric) -> Decimal | None:
    """取指标的可比较数值；"null" 或无法解析时返回 None（不参与比较）。"""
    if isinstance(metric.value, str) and metric.value.strip() == NULL_VALUE:
        return None
    try:
        return Decimal(str(metric.value))
    except InvalidOperation:
        return None


def calculate(
    request: AnalysisRequest,
    dataset: EngineDataset,
    *,
    kpi_metrics: Sequence[ResultMetric] = (),
) -> BenchmarkResult:
    """按 formula-spec §9 计算 F-BM-001（行业基准比较）。"""
    del dataset  # 基准数据来自 parameters，本计算器不消费数据集
    parameters = request.parameters
    industry = str(parameters.get("industry", ""))
    region = str(parameters.get("region") or DEFAULT_REGION)
    records, discarded = parse_benchmarks(parameters.get("benchmarks"))

    by_key: dict[tuple[str, str, str], list[BenchmarkRecord]] = {}
    for record in records:
        by_key.setdefault((record.industry, record.region, record.metric), []).append(record)

    comparisons: list[BenchmarkComparison] = []
    for metric in sorted(kpi_metrics, key=lambda item: item.name):
        candidates = by_key.get((industry, region, metric.name))
        if not candidates:
            continue
        merchant_value = _merchant_value(metric)
        if merchant_value is None:
            continue
        benchmark = _select_latest(candidates)
        relative = (
            (merchant_value - benchmark.value) / benchmark.value
            if benchmark.value != 0
            else None
        )
        comparisons.append(
            BenchmarkComparison(
                metric_name=metric.name,
                merchant_value=merchant_value,
                benchmark_value=benchmark.value,
                absolute_diff=merchant_value - benchmark.value,
                relative_diff=relative,
                benchmark=benchmark,
            )
        )

    scored = [item.relative_diff for item in comparisons if item.relative_diff is not None]
    if scored:
        deviation = sum(scored, Decimal(0)) / Decimal(len(scored))
        metric = build_metric(
            "F-BM-001",
            METRIC_NAMES["F-BM-001"],
            round_ratio(deviation),
            "ratio",
            len(scored),
        )
        warnings: tuple[Warning, ...] = ()
    else:
        metric = build_metric(
            "F-BM-001",
            METRIC_NAMES["F-BM-001"],
            NULL_VALUE,
            "ratio",
            0,
            reason="zero_benchmark_value" if comparisons else "benchmark_unavailable",
        )
        warnings = (
            Warning(
                code="BENCHMARK_UNAVAILABLE",
                severity=WarningSeverity.WARN,
                message=(
                    f"行业 {industry or '（未指定）'} / 地区 {region} 无可用基准数据，"
                    "不输出任何比较结论（禁止使用无来源说明的经验数字兜底）。"
                ),
                fields=[industry, region],
                blocking=False,
            ),
        )

    return BenchmarkResult(
        metrics=(metric,),
        warnings=warnings,
        comparisons=tuple(comparisons),
        discarded_records=discarded,
    )
