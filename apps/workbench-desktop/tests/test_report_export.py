"""报告导出测试（M1 Task 8.4）：导出器 / 用例 / 报告页。

覆盖 spec“报告导出”验收场景：
- HTML 报告：内联 CSS、run 元信息（期间/引擎/公式版本）、指标表与警告表
  （中文表头，警告只在 HTML 呈现）；
- CSV 报告：UTF-8 BOM（utf-8-sig，Excel 直接可读）、固定列 指标名/SKU/分类/值；
- report_artifact 登记：SHA-256 与产物文件一致；(run_id, format) 重复导出
  幂等更新（一条记录，不报错）；
- run 不存在：ReportExportError（用例层转中文消息返回）；
- 报告页：历史 run 列表载入、未选中提示、选中导出回显、重复导出“已更新”、
  打开目录入口与主窗口导航接线（打开目录经 monkeypatch 拦截，不真开资源管理器）。
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest
from contracts import AnalysisResult, InputSummary, ResultMetric, Warning
from local_data.repository import ReportArtifactRepository
from PySide6.QtGui import QDesktopServices
from sqlalchemy.orm import Session, sessionmaker
from workbench.application.analysis_usecase import RunAnalysisUseCase
from workbench.application.report_export import ReportExportManager
from workbench.infrastructure.db.result_store import SqlResultStore
from workbench.infrastructure.engine_adapter.providers import FakeEngineProvider
from workbench.infrastructure.report.exporter import (
    REPORTS_DIR_ENV,
    ReportExporter,
    ReportExportError,
    default_reports_dir,
    resolve_reports_dir,
)
from workbench.presentation.main_window import NAV_ITEMS, MainWindow
from workbench.presentation.report_page import ReportPage

#: 测试用 run_id（安全文件名主干与 run_id 一致，断言产物路径用）
RUN_ID = "run-report-0001"


def _make_result(run_id: str = RUN_ID) -> AnalysisResult:
    """构造带指标与警告的分析结果（HTML 内容断言用）。"""
    return AnalysisResult(
        schema_version="1.0",
        run_id=run_id,
        engine_version="0.1.0-fake",
        formula_version="0.1.0",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        metrics=[
            ResultMetric(
                name="KPI.OUTBOUND_QTY",
                value="60.5",
                unit="件",
                formula_id="F-KPI-003",
                formula_version="0.1.0",
                sample_count=3,
            )
        ],
        warnings=[
            Warning(
                code="ANALYSIS_PLACEHOLDER",
                severity="INFO",
                message="占位警告",
                fields=["metrics"],
                blocking=False,
            )
        ],
        summary="报告导出测试",
        input_summary=InputSummary(
            sku_count=1,
            movement_count=1,
            snapshot_count=0,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            dataset_digest="0" * 64,
        ),
    )


def _sha256_file(path: Path) -> str:
    """计算文件 SHA-256（产物登记一致性断言用）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture
def reports_dir(tmp_path: Path) -> Path:
    """报告产物目录（tmp 重定向，绝不写真实 %LOCALAPPDATA%）。"""
    return tmp_path / "reports"


@pytest.fixture
def exporter(
    session_factory: sessionmaker[Session], reports_dir: Path
) -> ReportExporter:
    """显式重定向报告目录的导出器。"""
    return ReportExporter(session_factory, reports_dir)


@pytest.fixture
def saved_run(store: SqlResultStore) -> str:
    """落库一条分析结果，返回 run_id。"""
    return store.save(_make_result())


# ---------------------------------------------------------------------------
# 报告目录解析（显式参数 > WORKBENCH_REPORTS_DIR > 默认目录）
# ---------------------------------------------------------------------------


def test_reports_dir_resolution_priority(tmp_path: Path, monkeypatch) -> None:
    """显式参数 > 环境变量 > 默认目录。"""
    explicit = tmp_path / "explicit"
    assert resolve_reports_dir(explicit) == explicit

    env_dir = tmp_path / "from-env"
    monkeypatch.setenv(REPORTS_DIR_ENV, str(env_dir))
    assert resolve_reports_dir(None) == env_dir

    monkeypatch.delenv(REPORTS_DIR_ENV, raising=False)
    assert resolve_reports_dir(None) == default_reports_dir()


# ---------------------------------------------------------------------------
# ReportExporter：HTML / CSV 内容与产物登记
# ---------------------------------------------------------------------------


def test_export_html_content_and_artifact(
    exporter: ReportExporter,
    saved_run: str,
    reports_dir: Path,
    session_factory: sessionmaker[Session],
) -> None:
    """HTML 报告：内联 CSS + 元信息 + 指标/警告表；产物登记 SHA-256 一致。"""
    outcome = exporter.export_html(saved_run)
    assert outcome.file_path == reports_dir / f"{RUN_ID}.html"
    assert outcome.updated is False

    text = outcome.file_path.read_text(encoding="utf-8")
    assert "仓库品类分析报告" in text
    assert RUN_ID in text
    assert "2026-08-01 ~ 2026-08-31" in text
    assert "0.1.0-fake" in text  # 引擎版本
    assert "<style>" in text  # 内联 CSS（无外部资源依赖）
    assert "指标名称" in text and "KPI.OUTBOUND_QTY" in text
    assert "警告码" in text and "ANALYSIS_PLACEHOLDER" in text  # 警告只在 HTML

    record = ReportArtifactRepository(session_factory).get(saved_run, "HTML")
    assert record is not None
    assert record.sha256 == _sha256_file(outcome.file_path)
    assert record.file_path == str(outcome.file_path)


def test_export_csv_bom_and_columns(
    exporter: ReportExporter, saved_run: str, reports_dir: Path
) -> None:
    """CSV 报告：UTF-8 BOM、固定列表头、指标行；警告不出现在 CSV。"""
    outcome = exporter.export_csv(saved_run)
    assert outcome.file_path == reports_dir / f"{RUN_ID}.csv"

    data = outcome.file_path.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM（utf-8-sig）

    text = data.decode("utf-8-sig")
    lines = [line for line in text.splitlines() if line]
    assert lines[0] == "指标名,SKU,分类,值"
    assert lines[1] == "KPI.OUTBOUND_QTY,,,60.5"  # M0 全库口径无 SKU/分类维度
    assert "ANALYSIS_PLACEHOLDER" not in text  # 警告只在 HTML 呈现


def test_reexport_same_format_is_idempotent_update(
    exporter: ReportExporter,
    saved_run: str,
    session_factory: sessionmaker[Session],
) -> None:
    """同一 (run_id, format) 重复导出：更新记录不报错，始终一条产物记录。"""
    first = exporter.export_html(saved_run)
    second = exporter.export_html(saved_run)
    assert first.updated is False
    assert second.updated is True

    records = ReportArtifactRepository(session_factory).list_for_run(saved_run)
    assert len(records) == 1
    assert records[0].sha256 == second.sha256  # 登记值与最新产物一致


def test_export_missing_run_raises(exporter: ReportExporter) -> None:
    """run 不存在：ReportExportError（中文消息）。"""
    with pytest.raises(ReportExportError, match="未找到分析运行"):
        exporter.export_html("run-not-exist")


# ---------------------------------------------------------------------------
# ReportExportManager：状态结构与消息
# ---------------------------------------------------------------------------


def test_manager_export_status_messages(
    store: SqlResultStore, exporter: ReportExporter
) -> None:
    """导出成功 / 重复导出 / 非法格式 / run 不存在 四类状态消息。"""
    manager = ReportExportManager(exporter)
    run_id = store.save(_make_result())

    ok = manager.export(run_id, "HTML")
    assert ok.ok is True
    assert "已导出 HTML 报告" in ok.message
    assert ok.file_path is not None and ok.file_path.exists()
    assert ok.sha256 is not None

    again = manager.export(run_id, "HTML")
    assert again.ok is True
    assert "已更新" in again.message

    bad_format = manager.export(run_id, "PDF")
    assert bad_format.ok is False
    assert "不支持的报告格式" in bad_format.message

    missing = manager.export("run-not-exist", "CSV")
    assert missing.ok is False
    assert "未找到分析运行" in missing.message


def test_manager_exposes_reports_dir(exporter: ReportExporter, reports_dir: Path) -> None:
    """reports_dir 透出（“打开所在目录”入口）。"""
    assert ReportExportManager(exporter).reports_dir == reports_dir


# ---------------------------------------------------------------------------
# ReportPage（pytest-qt offscreen）
# ---------------------------------------------------------------------------


def test_report_page_export_flow(
    qtbot,
    store: SqlResultStore,
    session_factory: sessionmaker[Session],
    reports_dir: Path,
) -> None:
    """报告页：列表载入 → 未选中提示 → 选中导出 HTML/CSV → 重复导出回显“已更新”。"""
    manager = ReportExportManager(ReportExporter(session_factory, reports_dir))
    run_id = store.save(_make_result())
    page = ReportPage(store, manager)
    qtbot.addWidget(page)

    # 历史 run 列表载入（run_id / 期间）
    assert page.run_table.rowCount() == 1
    assert page.run_table.item(0, 0).text() == run_id
    assert page.run_table.item(0, 2).text() == "2026-08-01 ~ 2026-08-31"

    # 未选中导出：提示先选择
    page.export_html_button.click()
    assert "请先在列表中选择" in page.status_label.text()

    # 选中后导出 HTML → 产物生成 + 状态回显
    page.run_table.selectRow(0)
    page.export_html_button.click()
    assert "已导出 HTML 报告" in page.status_label.text()
    assert (reports_dir / f"{run_id}.html").exists()

    # 导出 CSV → 产物生成
    page.export_csv_button.click()
    assert "已导出 CSV 报告" in page.status_label.text()
    assert (reports_dir / f"{run_id}.csv").exists()

    # 重复导出 HTML：幂等更新回显
    page.export_html_button.click()
    assert "已更新 HTML 报告" in page.status_label.text()


def test_report_page_refresh_after_new_run(
    qtbot,
    store: SqlResultStore,
    session_factory: sessionmaker[Session],
    reports_dir: Path,
) -> None:
    """刷新列表：新落库的 run 出现在列表首行（新 → 旧）。"""
    manager = ReportExportManager(ReportExporter(session_factory, reports_dir))
    first = store.save(_make_result("run-report-a0001"))
    page = ReportPage(store, manager)
    qtbot.addWidget(page)
    assert page.run_table.rowCount() == 1

    second = store.save(_make_result("run-report-b0002"))
    page.refresh_button.click()
    assert page.run_table.rowCount() == 2
    assert page.run_table.item(0, 0).text() == second
    assert page.run_table.item(1, 0).text() == first


def test_report_page_open_dir_shows_path(
    qtbot,
    store: SqlResultStore,
    session_factory: sessionmaker[Session],
    reports_dir: Path,
    monkeypatch,
) -> None:
    """打开所在目录：目录自动创建、状态栏回显路径（openUrl 拦截不真开窗口）。"""
    manager = ReportExportManager(ReportExporter(session_factory, reports_dir))
    store.save(_make_result())
    page = ReportPage(store, manager)
    qtbot.addWidget(page)

    opened: list[object] = []
    monkeypatch.setattr(
        QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url))
    )
    page.open_dir_button.click()

    assert "报告目录" in page.status_label.text()
    assert str(reports_dir) in page.status_label.text()
    assert reports_dir.exists()
    assert len(opened) == 1


def test_main_window_wires_report_page(
    qtbot,
    fake_provider: FakeEngineProvider,
    store: SqlResultStore,
    session_factory: sessionmaker[Session],
    reports_dir: Path,
    golden_input: Path,
) -> None:
    """主窗口：注入 report_manager 后“报告”导航挂真实报告页（非占位）。"""
    manager = ReportExportManager(ReportExporter(session_factory, reports_dir))
    window = MainWindow(
        RunAnalysisUseCase(fake_provider, store, golden_input),
        store,
        report_manager=manager,
    )
    qtbot.addWidget(window)

    assert window.report_page is not None
    assert window._pages.widget(NAV_ITEMS.index("报告")) is window.report_page
