"""报告导出用例（M1 Task 8）：包装 infrastructure 导出器供 presentation 调用。

- presentation 只依赖本模块（不 import infrastructure，分层与导入页一致）；
- 异常转结果结构：run 不存在等导出失败以 ``ReportExportStatus``
  （含中文消息）返回，UI 直接回显，无需 try/except；
- ``reports_dir`` 透出（报告页“打开所在目录”入口）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..infrastructure.report.exporter import (
    REPORT_FORMAT_CSV,
    REPORT_FORMAT_HTML,
    ReportExporter,
    ReportExportError,
)

#: 导出格式合法取值
EXPORT_FORMATS: tuple[str, ...] = (REPORT_FORMAT_HTML, REPORT_FORMAT_CSV)


@dataclass(frozen=True)
class ReportExportStatus:
    """一次导出的结果（成功带产物路径与 SHA-256，失败带中文消息）。"""

    ok: bool
    message: str
    file_path: Path | None = None
    sha256: str | None = None


class ReportExportManager:
    """报告导出用例：HTML / CSV 导出入口。"""

    def __init__(self, exporter: ReportExporter) -> None:
        self._exporter = exporter

    @property
    def reports_dir(self) -> Path:
        """报告产物目录（“打开所在目录”）。"""
        return self._exporter.reports_dir

    def export(self, run_id: str, fmt: str) -> ReportExportStatus:
        """导出指定格式报告；重复导出幂等更新记录（不报错）。"""
        try:
            if fmt == REPORT_FORMAT_HTML:
                outcome = self._exporter.export_html(run_id)
            elif fmt == REPORT_FORMAT_CSV:
                outcome = self._exporter.export_csv(run_id)
            else:
                return ReportExportStatus(ok=False, message=f"不支持的报告格式：{fmt}")
        except ReportExportError as exc:
            return ReportExportStatus(ok=False, message=str(exc))
        action = "已更新" if outcome.updated else "已导出"
        return ReportExportStatus(
            ok=True,
            message=f"{action} {fmt} 报告：{outcome.file_path}",
            file_path=outcome.file_path,
            sha256=outcome.sha256,
        )
