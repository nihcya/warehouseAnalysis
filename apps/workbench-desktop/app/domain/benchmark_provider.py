"""基准数据提供方端口：F-BM-001 的基准数据注入抽象（M2 Issue #10）。

引擎的基准比较计算器**不读文件、不访问网络**，基准记录必须由调用方经
``AnalysisRequest.parameters["benchmarks"]`` 注入（list[dict]），
另需 ``parameters["industry"]`` 与可选的 ``parameters["region"]`` 用于匹配。

本端口把「基准数据从哪来」从分析用例中解耦，使选择逻辑只存在于组合根
（``app.main``），与 DatasetProvider / EngineProvider 保持同样的依赖方向：

- JSON 文件实现（默认）：``infrastructure.benchmark.JsonBenchmarkProvider``
  ——读取版本化基准数据集 ``tests/fixtures/benchmarks/<version>.json``；
- 空实现（测试/未配置场景）：``NullBenchmarkProvider``——不注入任何参数，
  引擎据此发出 ``BENCHMARK_UNAVAILABLE`` 非阻断告警，而非崩溃。
"""

from __future__ import annotations

from typing import Protocol


class BenchmarkProvider(Protocol):
    """基准数据提供方：返回待并入 ``AnalysisRequest.parameters`` 的基准参数。"""

    @property
    def benchmark_version(self) -> str:
        """基准数据集版本标识（如 ``v0.1.0``），供 UI 与日志追溯；无数据时为空串。"""
        ...

    def load_parameters(self) -> dict[str, object]:
        """返回待并入请求 parameters 的基准参数字典。

        约定：

        - 无可用基准数据时返回**空字典**（不要抛异常）——引擎会发出
          ``BENCHMARK_UNAVAILABLE`` 告警，这正是期望的降级行为；
        - ``benchmarks`` 为记录字典列表，字段口径见
          ``docs/formula-spec.md`` §9（source / region / industry /
          sample_scope / updated_at / benchmark_version / unit /
          applicability / metric / value 十字段）；
        - 实现不得修改传入上下文，也不得感知 Qt。
        """
        ...
