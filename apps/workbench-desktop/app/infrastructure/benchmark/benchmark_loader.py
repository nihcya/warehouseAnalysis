"""基准数据注入实现（M2 Issue #10）：从版本化 JSON 数据集构造请求参数。

基准数据集由 B 侧随引擎交付（``tests/fixtures/benchmarks/<version>.json``），
引擎**不读文件、不访问网络**，故由本提供方载入后经
``AnalysisRequest.parameters["benchmarks"]`` 注入。

容错原则：任何读取/解析失败都退化为「无基准数据」（返回空字典），
由引擎发出 ``BENCHMARK_UNAVAILABLE`` 非阻断告警，绝不因配置问题阻断分析主流程。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...domain.benchmark_provider import BenchmarkProvider

#: 参数键：基准记录列表（引擎侧约定）
PARAM_BENCHMARKS = "benchmarks"

#: 参数键：行业（用于匹配 benchmark 记录的 industry 字段）
PARAM_INDUSTRY = "industry"

#: 参数键：区域（用于匹配 benchmark 记录的 region 字段，可缺省）
PARAM_REGION = "region"


class JsonBenchmarkProvider(BenchmarkProvider):
    """JSON 基准数据提供方：读取 ``{"benchmark_version", "records"}`` 结构文件。"""

    def __init__(
        self,
        path: Path,
        *,
        industry: str = "",
        region: str = "",
    ) -> None:
        self._path = Path(path)
        self._industry = industry
        self._region = region
        self._cached: tuple[str, list[dict[str, Any]]] | None = None

    @property
    def benchmark_version(self) -> str:
        """基准数据集版本；文件缺失或非法时为空串。"""
        return self._load()[0]

    def load_parameters(self) -> dict[str, object]:
        """返回基准参数；无可用数据时返回空字典（引擎降级为 BENCHMARK_UNAVAILABLE）。"""
        records = self._load()[1]
        if not records:
            return {}
        parameters: dict[str, object] = {PARAM_BENCHMARKS: records}
        if self._industry:
            parameters[PARAM_INDUSTRY] = self._industry
        if self._region:
            parameters[PARAM_REGION] = self._region
        return parameters

    def _load(self) -> tuple[str, list[dict[str, Any]]]:
        """载入并缓存（文件 I/O 只发生一次）。"""
        if self._cached is not None:
            return self._cached
        self._cached = self._read()
        return self._cached

    def _read(self) -> tuple[str, list[dict[str, Any]]]:
        """读取文件；任何失败都退化为空结果而非抛异常。"""
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ("", [])
        if not isinstance(payload, dict):
            return ("", [])
        version = str(payload.get("benchmark_version") or "")
        records = payload.get("records")
        if not isinstance(records, list):
            return (version, [])
        return (version, [row for row in records if isinstance(row, dict)])


class NullBenchmarkProvider(BenchmarkProvider):
    """空基准数据提供方：不注入任何参数（未配置/测试场景）。"""

    @property
    def benchmark_version(self) -> str:
        """无基准数据。"""
        return ""

    def load_parameters(self) -> dict[str, object]:
        """返回空字典：引擎据此发出 BENCHMARK_UNAVAILABLE 告警。"""
        return {}
