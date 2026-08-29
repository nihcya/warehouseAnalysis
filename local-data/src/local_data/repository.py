"""本地库仓储层：业务表的唯一 SQL 入口。

主基线 §35.3：数据库访问统一通过 Repository 与事务服务；
UI、Skill、报告模板一律不得拼接 SQL。

- AnalysisRepository（M0）：analysis_run / analysis_result，
  save_result 以“SUCCEEDED 运行 + full_result 单行”落库；
- MasterDataRepository（M1 Task 2）：SKU/仓库/库位/供应商/批次/条码
  主数据写入与按业务键查询、条码唯一映射校验；
- InventoryEventRepository（M1 Task 3）：事件幂等 upsert（重复
  event_id 跳过并计数，不报错）；StockSnapshotRepository：快照存取；
- ImportBatchRepository / ImportErrorRepository（M1 Task 4）：
  导入批次状态流转与错误行定位；
- ReportArtifactRepository（M1 Task 8）：报告产物 (run_id, format)
  幂等 upsert（重复导出 = 重新生成并更新记录）；
- BackupRecordRepository（M1 Task 9）：备份记录登记与状态流转
  （CREATED → VERIFIED / FAILED）。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from contracts import AnalysisResult
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from local_data.models import (
    BACKUP_STATUS_CREATED,
    BACKUP_STATUS_FAILED,
    BACKUP_STATUS_VERIFIED,
    IMPORT_STATUS_COMPLETED,
    IMPORT_STATUS_FAILED,
    IMPORT_STATUS_RUNNING,
    RUN_STATUS_SUCCEEDED,
    AnalysisResultRow,
    AnalysisRun,
    BackupRecordRow,
    BarcodeRow,
    ImportBatchRow,
    ImportErrorRow,
    InventoryEventRow,
    LocationRow,
    LotRow,
    ReportArtifactRow,
    SkuRow,
    StockSnapshotRow,
    SupplierRow,
    SupplierSkuRow,
    WarehouseRow,
    utc_now_iso,
)

#: M0 完整结果行的 result_type 约定（M1 拆分指标行时保留该类型以兼容）
FULL_RESULT_TYPE = "full_result"


class AnalysisRepository:
    """analysis_run / analysis_result 仓储。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_result(self, result: AnalysisResult, task_id: str | None = None) -> str:
        """保存完整分析结果，返回 run_id。

        - 单事务写入：analysis_run（SUCCEEDED）+ analysis_result 单行；
        - metric_json 存 ``result.model_dump_json()`` 原样序列化（金额为字符串，禁止 float 真值）；
        - warning_json 存 warnings 的 JSON 数组（与 metric_json 内 warnings 一致，便于直查）；
        - run_id 重复保存会触发 UNIQUE 冲突（IntegrityError），覆盖策略由调用方决定。
        """
        run_id = result.run_id
        now = utc_now_iso()
        run = AnalysisRun(
            run_id=run_id,
            task_id=task_id,
            start_date=result.period_start.isoformat(),
            end_date=result.period_end.isoformat(),
            scope_json=None,  # AnalysisResult 不含仓库范围，M0 置空
            engine_version=result.engine_version,
            formula_version=result.formula_version,
            status=RUN_STATUS_SUCCEEDED,
            started_at=now,
            finished_at=now,
            created_at=now,
            updated_at=now,
        )
        row = AnalysisResultRow(
            run_id=run_id,
            result_type=FULL_RESULT_TYPE,
            metric_json=result.model_dump_json(),
            warning_json=json.dumps(
                [warning.model_dump(mode="json") for warning in result.warnings],
                ensure_ascii=False,
            ),
            created_at=now,
        )
        with self._session_factory() as session, session.begin():
            session.add(run)
            session.add(row)
        return run_id

    def get_result(self, run_id: str) -> AnalysisResult | None:
        """按 run_id 取回完整结果；不存在或无 full_result 行时返回 None。"""
        with self._session_factory() as session:
            row = session.execute(
                select(AnalysisResultRow).where(
                    AnalysisResultRow.run_id == run_id,
                    AnalysisResultRow.result_type == FULL_RESULT_TYPE,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return AnalysisResult.model_validate_json(row.metric_json)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """按 run_id 取回运行元数据（报告导出需要期间与版本信息）。

        返回字段与 ``list_runs`` 的行结构一致；不存在返回 None。
        """
        with self._session_factory() as session:
            run = session.execute(
                select(AnalysisRun).where(AnalysisRun.run_id == run_id)
            ).scalar_one_or_none()
            if run is None:
                return None
            return {
                "run_id": run.run_id,
                "task_id": run.task_id,
                "start_date": run.start_date,
                "end_date": run.end_date,
                "engine_version": run.engine_version,
                "formula_version": run.formula_version,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "error_code": run.error_code,
                "created_at": run.created_at,
            }

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """最近运行的摘要列表（按创建时间倒序，默认最多 50 条）。"""
        statement = (
            select(AnalysisRun)
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
            .limit(limit)
        )
        with self._session_factory() as session:
            runs = session.execute(statement).scalars().all()
            return [
                {
                    "run_id": run.run_id,
                    "task_id": run.task_id,
                    "start_date": run.start_date,
                    "end_date": run.end_date,
                    "engine_version": run.engine_version,
                    "formula_version": run.formula_version,
                    "status": run.status,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "error_code": run.error_code,
                    "created_at": run.created_at,
                }
                for run in runs
            ]


class MasterDataRepository:
    """主数据仓储（Task 2）：sku/barcode/warehouse/location/supplier/supplier_sku/lot。

    M1 语义：create + read（更新/停用等由后续里程碑按需补充）；
    唯一约束冲突（重复 sku_id/barcode/组合键）由数据库抛 IntegrityError，
    覆盖策略由调用方决定。
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add_sku(
        self,
        *,
        sku_id: str,
        name: str,
        category: str | None = None,
        sub_category: str | None = None,
        unit: str | None = None,
        unit_scale: Decimal | None = None,
        unit_cost: Decimal | None = None,
        industry: str | None = None,
        is_active: bool = True,
    ) -> SkuRow:
        """写入一个 SKU；重复 sku_id 触发 UNIQUE 冲突（IntegrityError）。"""
        row = SkuRow(
            sku_id=sku_id,
            name=name,
            category=category,
            sub_category=sub_category,
            unit=unit,
            unit_scale=unit_scale,
            unit_cost=unit_cost,
            industry=industry,
            is_active=is_active,
        )
        with self._session_factory() as session, session.begin():
            session.add(row)
        return row

    def get_sku_by_sku_id(self, sku_id: str) -> SkuRow | None:
        """按业务键 sku_id 查询；不存在返回 None。"""
        with self._session_factory() as session:
            return session.execute(
                select(SkuRow).where(SkuRow.sku_id == sku_id)
            ).scalar_one_or_none()

    def add_barcode(
        self,
        *,
        barcode: str,
        sku_id: str,
        package_unit: str | None = None,
        conversion_factor: Decimal | None = None,
        is_active: bool = True,
    ) -> BarcodeRow:
        """写入条码映射；重复 barcode 触发 UNIQUE 冲突（IntegrityError）。"""
        row = BarcodeRow(
            barcode=barcode,
            sku_id=sku_id,
            package_unit=package_unit,
            conversion_factor=conversion_factor,
            is_active=is_active,
        )
        with self._session_factory() as session, session.begin():
            session.add(row)
        return row

    def get_active_sku_by_barcode(self, barcode: str) -> SkuRow | None:
        """条码唯一映射校验：一个 barcode 只映射一个有效 SKU。

        barcode 列 UNIQUE 保证至多一条映射；仅当映射行与 SKU 均为
        is_active 时返回 SKU，否则返回 None（视为无有效映射）。
        """
        with self._session_factory() as session:
            return session.execute(
                select(SkuRow)
                .join(BarcodeRow, BarcodeRow.sku_id == SkuRow.sku_id)
                .where(
                    BarcodeRow.barcode == barcode,
                    BarcodeRow.is_active.is_(True),
                    SkuRow.is_active.is_(True),
                )
            ).scalar_one_or_none()

    def add_warehouse(
        self,
        *,
        warehouse_id: str,
        name: str,
        address: str | None = None,
        is_active: bool = True,
    ) -> WarehouseRow:
        """写入仓库；重复 warehouse_id 触发 UNIQUE 冲突。"""
        row = WarehouseRow(
            warehouse_id=warehouse_id, name=name, address=address, is_active=is_active
        )
        with self._session_factory() as session, session.begin():
            session.add(row)
        return row

    def get_warehouse_by_warehouse_id(self, warehouse_id: str) -> WarehouseRow | None:
        """按业务键 warehouse_id 查询。"""
        with self._session_factory() as session:
            return session.execute(
                select(WarehouseRow).where(WarehouseRow.warehouse_id == warehouse_id)
            ).scalar_one_or_none()

    def add_location(
        self,
        *,
        warehouse_id: str,
        location_id: str,
        name: str | None = None,
        is_active: bool = True,
    ) -> LocationRow:
        """写入库位；同仓重复 location_id 触发组合 UNIQUE 冲突。"""
        row = LocationRow(
            warehouse_id=warehouse_id,
            location_id=location_id,
            name=name,
            is_active=is_active,
        )
        with self._session_factory() as session, session.begin():
            session.add(row)
        return row

    def add_supplier(
        self,
        *,
        supplier_id: str,
        name: str,
        contact: str | None = None,
        is_active: bool = True,
    ) -> SupplierRow:
        """写入供应商；重复 supplier_id 触发 UNIQUE 冲突。"""
        row = SupplierRow(
            supplier_id=supplier_id, name=name, contact=contact, is_active=is_active
        )
        with self._session_factory() as session, session.begin():
            session.add(row)
        return row

    def get_supplier_by_supplier_id(self, supplier_id: str) -> SupplierRow | None:
        """按业务键 supplier_id 查询。"""
        with self._session_factory() as session:
            return session.execute(
                select(SupplierRow).where(SupplierRow.supplier_id == supplier_id)
            ).scalar_one_or_none()

    def add_supplier_sku(
        self,
        *,
        supplier_id: str,
        sku_id: str,
        lead_time_days: int | None = None,
        moq: Decimal | None = None,
        pack_size: Decimal | None = None,
        order_cost: Decimal | None = None,
        holding_cost: Decimal | None = None,
        is_preferred: bool = False,
    ) -> SupplierSkuRow:
        """写入供应商-SKU 补货参数；负参数触发迁移内非负 CHECK 冲突。"""
        row = SupplierSkuRow(
            supplier_id=supplier_id,
            sku_id=sku_id,
            lead_time_days=lead_time_days,
            moq=moq,
            pack_size=pack_size,
            order_cost=order_cost,
            holding_cost=holding_cost,
            is_preferred=is_preferred,
        )
        with self._session_factory() as session, session.begin():
            session.add(row)
        return row

    def add_lot(
        self,
        *,
        sku_id: str,
        lot_id: str,
        production_date: str | None = None,
        expiry_date: str | None = None,
        received_at: str | None = None,
    ) -> LotRow:
        """写入批次（YYYY-MM-DD 日期文本 / UTC ISO 8601 时间文本）。

        expiry_date 早于 production_date 触发迁移内 CHECK 冲突（IntegrityError）。
        """
        row = LotRow(
            sku_id=sku_id,
            lot_id=lot_id,
            production_date=production_date,
            expiry_date=expiry_date,
            received_at=received_at,
        )
        with self._session_factory() as session, session.begin():
            session.add(row)
        return row


@dataclass(frozen=True)
class InventoryEventCreate:
    """待写入的库存事件（事实来源；数量恒为正，方向由 move_type 承载）。"""

    event_id: str
    sku_id: str
    warehouse_id: str
    move_type: str
    quantity: Decimal
    occurred_at: str  # UTC ISO 8601 文本
    source: str
    location_id: str | None = None
    lot_id: str | None = None
    unit_cost: Decimal | None = None
    source_ref: str | None = None
    reversal_of: str | None = None
    created_by: str | None = None


@dataclass(frozen=True)
class EventUpsertResult:
    """事件批量导入结果：新插入条数与幂等跳过条数。"""

    inserted: int
    skipped: int


class InventoryEventRepository:
    """库存事件仓储（Task 3）：event_id 幂等 upsert 与查询。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def upsert_events(self, events: Sequence[InventoryEventCreate]) -> EventUpsertResult:
        """批量幂等写入事件：已存在 event_id（含批内重复）跳过并计数，不报错。

        单主工作台单写入进程（§35.3），先查后插无并发竞争；
        重复 event_id 视为同一事实重复导入，静默跳过并计入 skipped。
        """
        incoming_ids = [event.event_id for event in events]
        with self._session_factory() as session, session.begin():
            existing_ids = set(
                session.execute(
                    select(InventoryEventRow.event_id).where(
                        InventoryEventRow.event_id.in_(incoming_ids)
                    )
                ).scalars()
            )
            seen: set[str] = set()
            inserted = 0
            skipped = 0
            for event in events:
                if event.event_id in existing_ids or event.event_id in seen:
                    skipped += 1
                    continue
                seen.add(event.event_id)
                session.add(InventoryEventRow(**asdict(event)))
                inserted += 1
        return EventUpsertResult(inserted=inserted, skipped=skipped)

    def get_event(self, event_id: str) -> InventoryEventRow | None:
        """按 event_id 查询事件。"""
        with self._session_factory() as session:
            return session.execute(
                select(InventoryEventRow).where(InventoryEventRow.event_id == event_id)
            ).scalar_one_or_none()

    def list_events(self) -> list[InventoryEventRow]:
        """全部事件，按 (occurred_at, event_id) 排序（投影回放顺序）。"""
        with self._session_factory() as session:
            return list(
                session.execute(
                    select(InventoryEventRow).order_by(
                        InventoryEventRow.occurred_at, InventoryEventRow.event_id
                    )
                ).scalars()
            )


class StockSnapshotRepository:
    """库存快照仓储（Task 3）：snapshot_id 存取往返。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_snapshot(
        self,
        *,
        snapshot_id: str,
        snapshot_date: str,
        sku_id: str,
        warehouse_id: str,
        quantity: Decimal,
        location_id: str | None = None,
        lot_id: str | None = None,
        inventory_value: Decimal | None = None,
        source: str | None = None,
    ) -> StockSnapshotRow:
        """写入一条快照；重复 snapshot_id 或五维组合触发 UNIQUE 冲突。"""
        row = StockSnapshotRow(
            snapshot_id=snapshot_id,
            snapshot_date=snapshot_date,
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            location_id=location_id,
            lot_id=lot_id,
            quantity=quantity,
            inventory_value=inventory_value,
            source=source,
        )
        with self._session_factory() as session, session.begin():
            session.add(row)
        return row

    def get_snapshot(self, snapshot_id: str) -> StockSnapshotRow | None:
        """按 snapshot_id 查询；不存在返回 None。"""
        with self._session_factory() as session:
            return session.execute(
                select(StockSnapshotRow).where(StockSnapshotRow.snapshot_id == snapshot_id)
            ).scalar_one_or_none()


class ImportBatchRepository:
    """导入批次仓储（Task 4）：RUNNING→COMPLETED/FAILED 状态流转与重复文件检出。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def start_batch(
        self,
        *,
        batch_id: str,
        file_name: str,
        file_hash: str,
        source_type: str,
        started_at: str | None = None,
    ) -> ImportBatchRow:
        """创建 RUNNING 批次（row_count / error_count 从 0 起算）。"""
        row = ImportBatchRow(
            batch_id=batch_id,
            file_name=file_name,
            file_hash=file_hash,
            source_type=source_type,
            started_at=started_at or utc_now_iso(),
            status=IMPORT_STATUS_RUNNING,
        )
        with self._session_factory() as session, session.begin():
            session.add(row)
        return row

    def complete_batch(
        self,
        batch_id: str,
        *,
        row_count: int,
        error_count: int,
        completed_at: str | None = None,
    ) -> ImportBatchRow | None:
        """批次完成：登记行数/错误数并置 COMPLETED；批次不存在返回 None。"""
        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(ImportBatchRow).where(ImportBatchRow.batch_id == batch_id)
            ).scalar_one_or_none()
            if row is None:
                return None
            row.status = IMPORT_STATUS_COMPLETED
            row.row_count = row_count
            row.error_count = error_count
            row.completed_at = completed_at or utc_now_iso()
            row.updated_at = utc_now_iso()
            return row

    def fail_batch(self, batch_id: str, *, completed_at: str | None = None) -> ImportBatchRow | None:
        """批次失败：置 FAILED 并记录结束时间；批次不存在返回 None。"""
        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(ImportBatchRow).where(ImportBatchRow.batch_id == batch_id)
            ).scalar_one_or_none()
            if row is None:
                return None
            row.status = IMPORT_STATUS_FAILED
            row.completed_at = completed_at or utc_now_iso()
            row.updated_at = utc_now_iso()
            return row

    def get_batch(self, batch_id: str) -> ImportBatchRow | None:
        """按 batch_id 查询批次。"""
        with self._session_factory() as session:
            return session.execute(
                select(ImportBatchRow).where(ImportBatchRow.batch_id == batch_id)
            ).scalar_one_or_none()

    def find_completed_by_file_hash(self, file_hash: str) -> ImportBatchRow | None:
        """按 file_hash 查最近一个已完成批次（重复导入提示用）。"""
        with self._session_factory() as session:
            return session.execute(
                select(ImportBatchRow)
                .where(
                    ImportBatchRow.file_hash == file_hash,
                    ImportBatchRow.status == IMPORT_STATUS_COMPLETED,
                )
                .order_by(ImportBatchRow.started_at.desc(), ImportBatchRow.id.desc())
                .limit(1)
            ).scalar_one_or_none()


class ImportErrorRepository:
    """导入错误行仓储（Task 4）：错误隔离落库与按 (batch_id, row_no) 定位。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add_error(
        self,
        *,
        batch_id: str,
        row_no: int,
        error_code: str,
        field_name: str | None = None,
        raw_value: str | None = None,
        suggestion: str | None = None,
        resolved_at: str | None = None,
    ) -> None:
        """写入一条错误行（raw_value/suggestion 原样保留，供错误修复闭环）。"""
        row = ImportErrorRow(
            batch_id=batch_id,
            row_no=row_no,
            field_name=field_name,
            error_code=error_code,
            raw_value=raw_value,
            suggestion=suggestion,
            resolved_at=resolved_at,
        )
        with self._session_factory() as session, session.begin():
            session.add(row)

    def list_errors(self, batch_id: str) -> list[ImportErrorRow]:
        """批次全部错误行，按 (row_no, id) 排序（错误页按行定位展示）。"""
        with self._session_factory() as session:
            return list(
                session.execute(
                    select(ImportErrorRow)
                    .where(ImportErrorRow.batch_id == batch_id)
                    .order_by(ImportErrorRow.row_no, ImportErrorRow.id)
                ).scalars()
            )

    def errors_for_row(self, batch_id: str, row_no: int) -> list[ImportErrorRow]:
        """定位某一行错误（同一行可能报多个字段错误）。"""
        with self._session_factory() as session:
            return list(
                session.execute(
                    select(ImportErrorRow).where(
                        ImportErrorRow.batch_id == batch_id,
                        ImportErrorRow.row_no == row_no,
                    )
                ).scalars()
            )


class ReportArtifactRepository:
    """报告产物仓储（M1 Task 8）：(run_id, format) 幂等 upsert 与查询。

    重复导出语义：导出幂等重生成相同内容，同一 (run_id, format) 重复导出
    = 覆盖报告文件 + 更新记录（UNIQUE 冲突不走报错路径而走更新路径），
    因此 report_artifact 始终与最新一次导出产物一致。
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def upsert(
        self,
        *,
        report_id: str,
        run_id: str,
        format: str,
        file_path: str,
        sha256: str,
    ) -> ReportArtifactRow:
        """登记一次导出产物：已存在同 (run_id, format) 记录时更新，否则插入。"""
        now = utc_now_iso()
        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(ReportArtifactRow).where(
                    ReportArtifactRow.run_id == run_id,
                    ReportArtifactRow.format == format,
                )
            ).scalar_one_or_none()
            if row is None:
                row = ReportArtifactRow(
                    report_id=report_id,
                    run_id=run_id,
                    format=format,
                    file_path=file_path,
                    sha256=sha256,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.report_id = report_id
                row.file_path = file_path
                row.sha256 = sha256
                row.updated_at = now
            session.flush()
            return row

    def get(self, run_id: str, format: str) -> ReportArtifactRow | None:
        """按 (run_id, format) 查询产物记录；不存在返回 None。"""
        with self._session_factory() as session:
            return session.execute(
                select(ReportArtifactRow).where(
                    ReportArtifactRow.run_id == run_id,
                    ReportArtifactRow.format == format,
                )
            ).scalar_one_or_none()

    def list_for_run(self, run_id: str) -> list[ReportArtifactRow]:
        """某次运行的全部产物记录（按创建先后排序）。"""
        with self._session_factory() as session:
            return list(
                session.execute(
                    select(ReportArtifactRow)
                    .where(ReportArtifactRow.run_id == run_id)
                    .order_by(ReportArtifactRow.id)
                ).scalars()
            )


class BackupRecordRepository:
    """备份记录仓储（M1 Task 9）：登记备份与状态流转。

    状态机：CREATED（文件已生成）→ VERIFIED（读回复验通过，填 verified_at）/
    FAILED（生成或复验失败）；恢复失败不落记录（当前库零改动），以返回结构上报。
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        *,
        backup_id: str,
        file_path: str,
        backup_type: str,
        db_schema_version: str,
        sha256: str,
        size_bytes: int,
        status: str = BACKUP_STATUS_CREATED,
        verified_at: str | None = None,
    ) -> BackupRecordRow:
        """登记一条备份记录（生成成功先 CREATED，复验后流转状态）。"""
        now = utc_now_iso()
        row = BackupRecordRow(
            backup_id=backup_id,
            file_path=file_path,
            backup_type=backup_type,
            db_schema_version=db_schema_version,
            sha256=sha256,
            size_bytes=size_bytes,
            status=status,
            verified_at=verified_at,
            created_at=now,
            updated_at=now,
        )
        with self._session_factory() as session, session.begin():
            session.add(row)
            session.flush()
            return row

    def mark_verified(self, backup_id: str, verified_at: str | None = None) -> BackupRecordRow | None:
        """复验通过：置 VERIFIED 并记录验证时间（备份成功必须有验证时间，§35.4）。"""
        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(BackupRecordRow).where(BackupRecordRow.backup_id == backup_id)
            ).scalar_one_or_none()
            if row is None:
                return None
            row.status = BACKUP_STATUS_VERIFIED
            row.verified_at = verified_at or utc_now_iso()
            row.updated_at = utc_now_iso()
            return row

    def mark_failed(self, backup_id: str) -> BackupRecordRow | None:
        """复验失败：置 FAILED（保留原 sha256 与行数供失败排查）。"""
        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(BackupRecordRow).where(BackupRecordRow.backup_id == backup_id)
            ).scalar_one_or_none()
            if row is None:
                return None
            row.status = BACKUP_STATUS_FAILED
            row.updated_at = utc_now_iso()
            return row

    def get_by_backup_id(self, backup_id: str) -> BackupRecordRow | None:
        """按 backup_id 查询记录。"""
        with self._session_factory() as session:
            return session.execute(
                select(BackupRecordRow).where(BackupRecordRow.backup_id == backup_id)
            ).scalar_one_or_none()

    def get_by_file_path(self, file_path: str) -> BackupRecordRow | None:
        """按备份文件路径查询记录（恢复时定位登记信息做校验值比对）。"""
        with self._session_factory() as session:
            return session.execute(
                select(BackupRecordRow).where(BackupRecordRow.file_path == file_path)
            ).scalar_one_or_none()

    def list_records(self, limit: int = 50) -> list[BackupRecordRow]:
        """最近备份记录列表（按创建时间倒序，默认最多 50 条）。"""
        with self._session_factory() as session:
            return list(
                session.execute(
                    select(BackupRecordRow)
                    .order_by(BackupRecordRow.created_at.desc(), BackupRecordRow.id.desc())
                    .limit(limit)
                ).scalars()
            )
