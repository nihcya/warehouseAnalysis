"""报告导出器（M1 Task 8）：HTML 与 CSV 两种格式的纯逻辑导出（不依赖 Qt）。

- 输入：run 元数据 + AnalysisResult（经 ``local_data.repository`` 读取，
  SQL 唯一入口在 Repository 层，本模块不拼接 SQL）；
- 输出：``%LOCALAPPDATA%\\WarehouseWorkbench\\reports\\{run_id}.{html|csv}``
  （解析优先级与 ``local_data.connection`` 一致：显式参数 >
  ``WORKBENCH_REPORTS_DIR`` > 默认目录；测试用显式参数重定向 tmp）；
- 写入后计算 SHA-256 并 upsert ``report_artifact``；
- 重复导出语义：导出幂等重生成相同内容，同一 (run_id, format) 重复导出
  = 覆盖报告文件 + 更新记录（UNIQUE 冲突走更新路径而非报错，
  见 ``ReportArtifactRepository.upsert``）；
- HTML：内联 CSS 字符串拼接模板（不引 Jinja2），含 run_id、期间、
  引擎/公式版本、指标表与 Warning 表（中文表头）；
- CSV：UTF-8 with BOM（``utf-8-sig``，Excel 可直接打开）；列固定为
  指标名/SKU/分类/值；Warning 只在 HTML 呈现，CSV 保持纯指标。
"""

from __future__ import annotations

import csv
import hashlib
import html
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contracts import AnalysisResult
from local_data.models import (
    REPORT_FORMAT_CSV,
    REPORT_FORMAT_HTML,
    AnalysisResultRow,
    utc_now_iso,
)
from local_data.repository import (
    FULL_RESULT_TYPE,
    AnalysisRepository,
    ReportArtifactRepository,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

#: 报告子目录名（与 local_data.connection 的数据目录约定同级）
REPORTS_SUBDIR_NAME = "reports"

#: 报告目录重定向环境变量（测试与便携模式）
REPORTS_DIR_ENV = "WORKBENCH_REPORTS_DIR"

#: 本地应用目录名（与 local_data.connection.APP_DIR_NAME 保持一致）
_APP_DIR_NAME = "WarehouseWorkbench"

#: run_id 中允许直接作为文件名的字符（其余替换为下划线，防路径穿越/非法字符）
_SAFE_RUN_ID = re.compile(r"[^A-Za-z0-9._-]+")


class ReportExportError(Exception):
    """报告导出失败（run 不存在、目录不可写等），消息为中文可直接展示。"""


def default_reports_dir() -> Path:
    """默认报告目录：%LOCALAPPDATA%\\WarehouseWorkbench\\reports。"""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        # 非 Windows 或未设置 LOCALAPPDATA 时退化为用户主目录
        base = Path.home()
    return base / _APP_DIR_NAME / REPORTS_SUBDIR_NAME


def resolve_reports_dir(reports_dir: Path | None = None) -> Path:
    """按“显式参数 > WORKBENCH_REPORTS_DIR > 默认目录”解析报告目录。"""
    if reports_dir is not None:
        return reports_dir
    env_dir = os.environ.get(REPORTS_DIR_ENV)
    if env_dir:
        return Path(env_dir)
    return default_reports_dir()


def _safe_file_stem(run_id: str) -> str:
    """run_id 转安全文件名主干（非法字符替换为下划线）。"""
    stem = _SAFE_RUN_ID.sub("_", run_id).strip("._") or "run"
    return stem


def _sha256_file(path: Path) -> str:
    """计算文件 SHA-256（报告产物登记与完整性校验值）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


#: HTML 内联 CSS（简单表格模板，中文表头，无外部资源依赖）
_HTML_CSS = """\
body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; margin: 24px;
       color: #222; background: #fafafa; }
h1 { font-size: 20px; border-bottom: 2px solid #2c6fbb; padding-bottom: 6px; }
.meta { margin: 8px 0 20px; color: #555; font-size: 13px; line-height: 1.7; }
h2 { font-size: 16px; margin-top: 24px; }
table { border-collapse: collapse; width: 100%; background: #fff; font-size: 13px; }
th, td { border: 1px solid #d9d9d9; padding: 6px 10px; text-align: left; }
th { background: #eef3fa; }
tr:nth-child(even) td { background: #f7f9fc; }
.warn-code { font-weight: bold; }
"""


@dataclass(frozen=True)
class ReportExportOutcome:
    """一次导出的结果：产物路径、SHA-256 与记录是否为更新。"""

    run_id: str
    format: str
    file_path: Path
    sha256: str
    updated: bool  # True = 重复导出走记录更新路径


class ReportExporter:
    """HTML / CSV 报告导出器（组合根注入 session_factory 与报告目录）。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        reports_dir: Path | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._reports_dir = resolve_reports_dir(reports_dir)
        self._runs = AnalysisRepository(session_factory)
        self._artifacts = ReportArtifactRepository(session_factory)

    @property
    def reports_dir(self) -> Path:
        """报告产物输出目录（“打开所在目录”入口）。"""
        return self._reports_dir

    def export_html(self, run_id: str) -> ReportExportOutcome:
        """导出 HTML 报告；run 不存在抛 ReportExportError。"""
        return self._export(run_id, REPORT_FORMAT_HTML, self._render_html)

    def export_csv(self, run_id: str) -> ReportExportOutcome:
        """导出 CSV 报告（UTF-8 with BOM，纯指标）；run 不存在抛 ReportExportError。"""
        return self._export(run_id, REPORT_FORMAT_CSV, self._render_csv)

    # ------------------------------------------------------------------
    # 内部：读取 → 渲染 → 写文件 → 登记
    # ------------------------------------------------------------------

    def _export(
        self,
        run_id: str,
        format: str,
        render: Callable[[Path, dict[str, Any], AnalysisResult], None],
    ) -> ReportExportOutcome:
        """通用导出流程：查 run → 渲染写文件 → 算 sha256 → upsert 产物记录。"""
        run = self._runs.get_run(run_id)
        result = self._runs.get_result(run_id)
        if run is None or result is None:
            raise ReportExportError(f"未找到分析运行：{run_id}（无法导出报告）")

        self._reports_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._reports_dir / f"{_safe_file_stem(run_id)}.{format.lower()}"
        existing = self._artifacts.get(run_id, format)
        render(file_path, run, result)

        sha256 = _sha256_file(file_path)
        self._artifacts.upsert(
            report_id=f"report-{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            format=format,
            file_path=str(file_path),
            sha256=sha256,
        )
        return ReportExportOutcome(
            run_id=run_id,
            format=format,
            file_path=file_path,
            sha256=sha256,
            updated=existing is not None,
        )

    def _render_html(self, path: Path, run: dict[str, Any], result: AnalysisResult) -> None:
        """渲染 HTML 报告：元信息 + 指标表 + Warning 表（内联 CSS，中文表头）。"""
        start = run.get("start_date") or result.period_start.isoformat()
        end = run.get("end_date") or result.period_end.isoformat()
        lines: list[str] = [
            "<!DOCTYPE html>",
            '<html lang="zh-CN">',
            '<head><meta charset="utf-8">',
            f"<title>分析报告 {html.escape(result.run_id)}</title>",
            f"<style>{_HTML_CSS}</style>",
            "</head><body>",
            "<h1>仓库品类分析报告</h1>",
            '<div class="meta">',
            f"运行编号：{html.escape(result.run_id)}<br>",
            f"分析期间：{html.escape(start)} ~ {html.escape(end)}<br>",
            f"引擎版本：{html.escape(result.engine_version)}<br>",
            f"公式版本：{html.escape(result.formula_version)}<br>",
            f"生成时间：{html.escape(utc_now_iso())}",
            "</div>",
            "<h2>指标</h2>",
            "<table>",
            (
                "<tr><th>指标名称</th><th>数值</th><th>单位</th>"
                "<th>公式标识</th><th>公式版本</th><th>样本数</th></tr>"
            ),
        ]
        for metric in result.metrics:
            lines.append(
                "<tr>"
                f"<td>{html.escape(str(metric.name))}</td>"
                f"<td>{html.escape(str(metric.value))}</td>"
                f"<td>{html.escape(str(metric.unit))}</td>"
                f"<td>{html.escape(str(metric.formula_id))}</td>"
                f"<td>{html.escape(str(metric.formula_version))}</td>"
                f"<td>{html.escape(str(metric.sample_count))}</td>"
                "</tr>"
            )
        if not result.metrics:
            lines.append('<tr><td colspan="6">（无指标）</td></tr>')
        lines.append("</table>")

        lines.append("<h2>警告</h2>")
        lines.append("<table>")
        lines.append("<tr><th>警告码</th><th>级别</th><th>说明</th>"
                     "<th>涉及字段</th><th>是否阻断</th></tr>")
        for warning in result.warnings:
            lines.append(
                "<tr>"
                f'<td class="warn-code">{html.escape(str(warning.code))}</td>'
                f"<td>{html.escape(str(warning.severity))}</td>"
                f"<td>{html.escape(str(warning.message))}</td>"
                f"<td>{html.escape(', '.join(warning.fields) or '—')}</td>"
                f"<td>{'是' if warning.blocking else '否'}</td>"
                "</tr>"
            )
        if not result.warnings:
            lines.append('<tr><td colspan="5">（无警告）</td></tr>')
        lines.append("</table>")

        lines.append("</body></html>")
        path.write_text("\n".join(lines), encoding="utf-8")

    def _render_csv(self, path: Path, run: dict[str, Any], result: AnalysisResult) -> None:
        """渲染 CSV 报告：UTF-8 with BOM；列固定 指标名/SKU/分类/值。

        SKU/分类取 ``analysis_result`` 行的业务维度（M0 full_result 单行
        为全库口径，维度为空）；Warning 只在 HTML 呈现，CSV 保持纯指标。
        """
        row = self._find_full_result_row(result.run_id)
        sku_id = row.sku_id if row is not None and row.sku_id else ""
        category = row.category if row is not None and row.category else ""
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["指标名", "SKU", "分类", "值"])
            for metric in result.metrics:
                writer.writerow([metric.name, sku_id, category, str(metric.value)])

    def _find_full_result_row(self, run_id: str) -> AnalysisResultRow | None:
        """取 full_result 结果行（读取其 sku_id/category 维度）。"""
        with self._session_factory() as session:
            return session.execute(
                select(AnalysisResultRow).where(
                    AnalysisResultRow.run_id == run_id,
                    AnalysisResultRow.result_type == FULL_RESULT_TYPE,
                )
            ).scalar_one_or_none()
