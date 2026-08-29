"""CSV 导入用例（M1 Task 5）：编码探测 → 字段映射 → 逐行校验 → 错误隔离提交。

- 纯逻辑与 Qt 分离：本模块不 import PySide6，可独立于 UI 测试；
- 编码探测：先 UTF-8（含 BOM，``utf-8-sig`` 自动剥离），失败退 GBK，
  再失败以 ``IMPORT_ENCODING_FAILED`` 阻断（M1 不引入更多编码）；
- 字段映射：契约字段 → CSV 列名，由调用方（向导 UI）生成传入；
  缺必填映射以 ``MISSING_REQUIRED_COLUMN`` 阻断，不创建导入批次；
- 逐行校验：数量 > 0、Decimal 可解析、日期 YYYY-MM-DD、move_type 在
  枚举内、SKU/仓库存在性（事件导入，外键事实保护）、unit_cost 非负；
  失败行构造错误记录（row_no 自数据首行 = 2 起（表头为第 1 行）、
  field_name、error_code、raw_value、suggestion 中文修复建议）；
- 错误隔离：非法行进 ``import_error``，合法行正常入库，互不阻塞；
- 事件导入走 ``InventoryEventRepository`` 幂等 upsert（重复 event_id 计入
  skipped 并入批次统计），主数据导入 create-only（重复 sku_id 记错误行）；
- 事件导入成功后经 ``BalanceProjectionService`` 重建余额投影；
- 重复导入检测：file_hash 命中 COMPLETED 历史批次返回
  ``DuplicateImportFound``（不静默重复；M1 不提供强制覆盖）。

move_type 合法取值采用 ``local_data.models.MOVE_TYPES``（DB 落库与投影
语义的唯一权威；契约 ``contracts.MoveType`` 的 TRANSFER/STOCKTAKE 不在
本地库口径内，校验阶段即拦截并给出可修复建议）。
"""

from __future__ import annotations

import csv
import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from local_data.models import (
    IMPORT_STATUS_COMPLETED,
    IMPORT_STATUS_FAILED,
    MOVE_TYPES,
)
from local_data.projection import BalanceProjectionService
from local_data.repository import (
    ImportBatchRepository,
    ImportErrorRepository,
    InventoryEventCreate,
    InventoryEventRepository,
    MasterDataRepository,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

#: 导入类型：主数据（SKU）
IMPORT_TYPE_MASTER = "master_data"
#: 导入类型：库存事件
IMPORT_TYPE_EVENTS = "inventory_events"
#: 全部合法导入类型
IMPORT_TYPES: tuple[str, ...] = (IMPORT_TYPE_MASTER, IMPORT_TYPE_EVENTS)

#: import_batch.source_type 取值（M1 仅 CSV）
SOURCE_TYPE_CSV = "CSV"
#: 事件导入的 source 标记（inventory_event.source 约定：IMPORT_CSV / UI）
EVENT_SOURCE_CSV = "IMPORT_CSV"

# ---------------------------------------------------------------------------
# 错误码（字符串常量，import_error.error_code 落库值）
# ---------------------------------------------------------------------------

ERROR_QUANTITY_INVALID = "QUANTITY_INVALID"
ERROR_SKU_NOT_FOUND = "SKU_NOT_FOUND"
ERROR_DATE_INVALID = "DATE_INVALID"
ERROR_MOVE_TYPE_INVALID = "MOVE_TYPE_INVALID"
ERROR_DECIMAL_INVALID = "DECIMAL_INVALID"
ERROR_UNIT_COST_INVALID = "UNIT_COST_INVALID"
ERROR_WAREHOUSE_NOT_FOUND = "WAREHOUSE_NOT_FOUND"
ERROR_REQUIRED_MISSING = "REQUIRED_FIELD_MISSING"
ERROR_SKU_DUPLICATE = "SKU_DUPLICATE"
ERROR_ENCODING = "IMPORT_ENCODING_FAILED"
ERROR_MISSING_COLUMN = "MISSING_REQUIRED_COLUMN"
ERROR_EMPTY_FILE = "EMPTY_FILE"

#: 各错误码的中文修复建议（向导错误明细页展示）
_SUGGESTIONS: Mapping[str, str] = {
    ERROR_QUANTITY_INVALID: "数量必须大于 0（方向由 move_type 表达，数量恒为正）",
    ERROR_SKU_NOT_FOUND: "先导入该 SKU 的主数据，再导入库存事件",
    ERROR_DATE_INVALID: "日期须为 YYYY-MM-DD 格式（如 2026-08-29）",
    ERROR_MOVE_TYPE_INVALID: f"事件类型须为 {' / '.join(MOVE_TYPES)} 之一",
    ERROR_DECIMAL_INVALID: "数值须为合法十进制数（如 12.5）",
    ERROR_UNIT_COST_INVALID: "单位成本必须为非负数值",
    ERROR_WAREHOUSE_NOT_FOUND: "先在主数据中建立该仓库，再导入库存事件",
    ERROR_REQUIRED_MISSING: "必填字段不能为空",
    ERROR_SKU_DUPLICATE: "SKU 已存在（M1 不支持覆盖更新，请勿重复导入）",
}

#: 主数据导入必填契约字段（缺映射即阻断）
MASTER_REQUIRED_FIELDS: tuple[str, ...] = ("sku_id", "name")
#: 主数据导入可选契约字段
MASTER_OPTIONAL_FIELDS: tuple[str, ...] = (
    "category",
    "sub_category",
    "unit",
    "unit_cost",
    "industry",
)
#: 事件导入必填契约字段
EVENT_REQUIRED_FIELDS: tuple[str, ...] = (
    "event_id",
    "sku_id",
    "warehouse_id",
    "move_type",
    "quantity",
    "occurred_at",
)
#: 事件导入可选契约字段
EVENT_OPTIONAL_FIELDS: tuple[str, ...] = ("unit_cost", "source_ref")

#: 字段映射：契约字段 → CSV 列名（向导生成后传入）
FieldMapping = dict[str, str]

#: 编码探测候选（utf-8-sig 兼容含/不含 BOM 的 UTF-8）
ENCODING_CANDIDATES: tuple[str, ...] = ("utf-8-sig", "gbk")
#: 探测结果展示名
ENCODING_UTF8 = "UTF-8"
ENCODING_GBK = "GBK"


class CsvImportError(Exception):
    """导入前置检查失败（编码探测/表头/必填映射缺失），导入被阻断。"""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


@dataclass(frozen=True)
class CsvSnapshot:
    """CSV 读取快照：编码 + 表头 + 数据行（向导预览与映射的输入）。"""

    path: Path
    encoding: str
    fieldnames: list[str]
    rows: list[dict[str, str | None]]


@dataclass(frozen=True)
class ErrorRecord:
    """一条导入错误：与 import_error 行同构（五要素）。"""

    row_no: int
    error_code: str
    field_name: str | None = None
    raw_value: str | None = None
    suggestion: str | None = None


@dataclass(frozen=True)
class SkuDraft:
    """待写入的 SKU 主数据草稿（row_no 用于错误行定位）。"""

    row_no: int
    sku_id: str
    name: str
    category: str | None = None
    sub_category: str | None = None
    unit: str | None = None
    unit_cost: Decimal | None = None
    industry: str | None = None


@dataclass(frozen=True)
class ImportRunSummary:
    """一次导入执行的汇总（批次登记结果，向导完成页展示）。"""

    batch_id: str
    status: str  # IMPORT_STATUSES 取值
    row_count: int
    inserted: int
    skipped: int
    error_count: int

    @property
    def completed(self) -> bool:
        """批次状态是否 COMPLETED（展示层中文文案映射用）。"""
        return self.status == IMPORT_STATUS_COMPLETED


@dataclass(frozen=True)
class DuplicateImportFound:
    """重复文件导入被阻断：指向同 file_hash 的历史 COMPLETED 批次。"""

    batch_id: str
    file_hash: str


def detect_encoding(path: Path) -> str:
    """编码探测：先 UTF-8（含 BOM），失败退 GBK；均失败抛 ``CsvImportError``。"""
    data = path.read_bytes()
    for candidate in ENCODING_CANDIDATES:
        try:
            data.decode(candidate)
        except UnicodeDecodeError:
            continue
        return ENCODING_UTF8 if candidate == "utf-8-sig" else ENCODING_GBK
    raise CsvImportError(
        ERROR_ENCODING,
        f"无法识别文件编码（已尝试 UTF-8 / GBK）：{path.name}",
    )


def _codec_for(display: str) -> str:
    """展示名 → 实际 codec（UTF-8 统一用 utf-8-sig 读取，自动剥离 BOM）。"""
    return "utf-8-sig" if display == ENCODING_UTF8 else display


def _file_sha256(path: Path) -> str:
    """文件 SHA-256（分块读取；重复导入检测依据）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cell(row: Mapping[str, str | None], column: str | None) -> str | None:
    """取映射列文本；strip 后空串归一为 None。"""
    if column is None:
        return None
    text = row.get(column)
    if text is None:
        return None
    stripped = text.strip()
    return stripped or None


def _raw(row: Mapping[str, str | None], column: str | None) -> str | None:
    """取原始单元格文本（未 strip，供 raw_value 落库定位）。"""
    if column is None:
        return None
    return row.get(column)


def _parse_decimal(raw: str) -> Decimal | None:
    """解析十进制文本；非法或非有限（NaN / Infinity）返回 None。"""
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return number


def _parse_iso_date(value: str) -> date | None:
    """严格解析 YYYY-MM-DD（非补零格式如 2026-8-9 视为非法）。"""
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    if parsed.isoformat() != value:
        return None
    return parsed


def _to_utc_iso(day: date) -> str:
    """YYYY-MM-DD → UTC ISO 8601 文本（当日零点，与库内事件口径一致）。"""
    return f"{day.isoformat()}T00:00:00+00:00"


def _field_error(
    row_no: int,
    field_name: str,
    error_code: str,
    raw_value: str | None,
) -> ErrorRecord:
    """构造带中文修复建议的错误记录。"""
    return ErrorRecord(
        row_no=row_no,
        error_code=error_code,
        field_name=field_name,
        raw_value=raw_value,
        suggestion=_SUGGESTIONS.get(error_code),
    )


#: 必填校验失败时的占位值（所在行将因错误被丢弃，永不落库）
_PLACEHOLDER_DATE = date(1970, 1, 1)


class CsvImportManager:
    """CSV 导入用例：读取/探测供向导使用，``run_import`` 执行完整导入。

    依赖 local-data 的 Repository / ProjectionService（只消费不修改）；
    SQL 唯一入口在仓储层（§35.3），本类不含任何 SQL。
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._batches = ImportBatchRepository(session_factory)
        self._errors = ImportErrorRepository(session_factory)
        self._master = MasterDataRepository(session_factory)
        self._events = InventoryEventRepository(session_factory)
        self._projection = BalanceProjectionService(session_factory)

    # ------------------------------------------------------------------
    # 读取与探测（向导文件选择/映射/预览页使用）
    # ------------------------------------------------------------------

    def read_csv(self, path: Path) -> CsvSnapshot:
        """探测编码并以 ``csv.DictReader`` 读取 CSV 为快照。"""
        display = detect_encoding(path)
        with path.open("r", encoding=_codec_for(display), newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            if not fieldnames:
                raise CsvImportError(ERROR_EMPTY_FILE, f"文件没有表头行：{path.name}")
            rows = [dict(row) for row in reader]
        return CsvSnapshot(path=path, encoding=display, fieldnames=fieldnames, rows=rows)

    def list_errors(self, batch_id: str) -> list[ErrorRecord]:
        """按批次读取错误明细（向导错误页数据源，import_error 查询）。"""
        return [
            ErrorRecord(
                row_no=row.row_no,
                error_code=row.error_code,
                field_name=row.field_name,
                raw_value=row.raw_value,
                suggestion=row.suggestion,
            )
            for row in self._errors.list_errors(batch_id)
        ]

    # ------------------------------------------------------------------
    # 导入执行
    # ------------------------------------------------------------------

    def run_import(
        self,
        *,
        path: Path,
        import_type: str,
        mapping: FieldMapping,
    ) -> ImportRunSummary | DuplicateImportFound:
        """执行一次完整导入：读取 → 校验 → 错误隔离提交 → 批次登记。

        - 前置失败（编码/表头/必填映射缺失）抛 ``CsvImportError``，不创建批次；
        - 重复导入（file_hash 命中 COMPLETED 批次）返回 ``DuplicateImportFound``，
          不静默重复入库（M1 不提供强制覆盖）。
        """
        if import_type not in IMPORT_TYPES:
            raise ValueError(f"未知导入类型：{import_type}")
        snapshot = self.read_csv(path)
        self._require_columns(import_type, snapshot, mapping)

        file_hash = _file_sha256(path)
        duplicate = self._batches.find_completed_by_file_hash(file_hash)
        if duplicate is not None:
            return DuplicateImportFound(batch_id=duplicate.batch_id, file_hash=file_hash)

        batch_id = f"IMP-{uuid.uuid4().hex[:12]}"
        self._batches.start_batch(
            batch_id=batch_id,
            file_name=path.name,
            file_hash=file_hash,
            source_type=SOURCE_TYPE_CSV,
        )

        if import_type == IMPORT_TYPE_MASTER:
            drafts, errors = self._validate_master_rows(snapshot, mapping)
            inserted, skipped, commit_errors = self._commit_master(drafts)
        else:
            events, errors = self._validate_event_rows(snapshot, mapping)
            inserted, skipped, commit_errors = self._commit_events(events)
        errors = errors + commit_errors

        row_count = len(snapshot.rows)
        error_count = len(errors)
        for record in errors:
            self._errors.add_error(
                batch_id=batch_id,
                row_no=record.row_no,
                error_code=record.error_code,
                field_name=record.field_name,
                raw_value=record.raw_value,
                suggestion=record.suggestion,
            )

        # complete_batch 是行数/错误数登记的唯一入口；
        # 全部行失败时随后置 FAILED（repo 无带计数的 fail 接口，不改 local-data）
        self._batches.complete_batch(batch_id, row_count=row_count, error_count=error_count)
        status = IMPORT_STATUS_COMPLETED
        if row_count > 0 and error_count == row_count:
            self._batches.fail_batch(batch_id)
            status = IMPORT_STATUS_FAILED

        return ImportRunSummary(
            batch_id=batch_id,
            status=status,
            row_count=row_count,
            inserted=inserted,
            skipped=skipped,
            error_count=error_count,
        )

    # ------------------------------------------------------------------
    # 内部：前置检查与逐行校验
    # ------------------------------------------------------------------

    def _require_columns(
        self,
        import_type: str,
        snapshot: CsvSnapshot,
        mapping: FieldMapping,
    ) -> None:
        """缺必填字段映射（或映射列不在表头）时报错阻断。"""
        required = (
            MASTER_REQUIRED_FIELDS
            if import_type == IMPORT_TYPE_MASTER
            else EVENT_REQUIRED_FIELDS
        )
        missing = [
            field
            for field in required
            if not mapping.get(field) or mapping[field] not in snapshot.fieldnames
        ]
        if missing:
            label = "主数据" if import_type == IMPORT_TYPE_MASTER else "库存事件"
            raise CsvImportError(
                ERROR_MISSING_COLUMN,
                f"缺少必填字段映射：{'、'.join(missing)}（{label}导入）",
            )

    def _required_text(
        self,
        row: Mapping[str, str | None],
        mapping: FieldMapping,
        field: str,
        row_no: int,
        errors: list[ErrorRecord],
    ) -> str:
        """取必填文本；空值记 REQUIRED_FIELD_MISSING 并返回占位空串。"""
        column = mapping.get(field)
        value = _cell(row, column)
        if value is None:
            errors.append(
                _field_error(row_no, field, ERROR_REQUIRED_MISSING, _raw(row, column))
            )
            return ""
        return value

    def _optional_text(
        self,
        row: Mapping[str, str | None],
        mapping: FieldMapping,
        field: str,
    ) -> str | None:
        """取可选文本；空值返回 None（不记错误）。"""
        return _cell(row, mapping.get(field))

    def _optional_unit_cost(
        self,
        row: Mapping[str, str | None],
        mapping: FieldMapping,
        row_no: int,
        errors: list[ErrorRecord],
    ) -> Decimal | None:
        """取可选单位成本：Decimal 可解析且非负；违例记错误。"""
        column = mapping.get("unit_cost")
        raw = _cell(row, column)
        if raw is None:
            return None
        number = _parse_decimal(raw)
        if number is None:
            errors.append(_field_error(row_no, "unit_cost", ERROR_DECIMAL_INVALID, raw))
            return None
        if number < 0:
            errors.append(_field_error(row_no, "unit_cost", ERROR_UNIT_COST_INVALID, raw))
            return None
        return number

    def _required_quantity(
        self,
        row: Mapping[str, str | None],
        mapping: FieldMapping,
        row_no: int,
        errors: list[ErrorRecord],
    ) -> Decimal:
        """取必填数量：Decimal 可解析且 > 0；违例记错误并返回占位 0。"""
        column = mapping.get("quantity")
        raw = _cell(row, column)
        if raw is None:
            errors.append(
                _field_error(row_no, "quantity", ERROR_REQUIRED_MISSING, _raw(row, column))
            )
            return Decimal(0)
        number = _parse_decimal(raw)
        if number is None:
            errors.append(_field_error(row_no, "quantity", ERROR_DECIMAL_INVALID, raw))
            return Decimal(0)
        if number <= 0:
            errors.append(_field_error(row_no, "quantity", ERROR_QUANTITY_INVALID, raw))
            return Decimal(0)
        return number

    def _required_date(
        self,
        row: Mapping[str, str | None],
        mapping: FieldMapping,
        row_no: int,
        errors: list[ErrorRecord],
    ) -> date:
        """取必填日期：严格 YYYY-MM-DD；违例记错误并返回占位日期。"""
        column = mapping.get("occurred_at")
        raw = _cell(row, column)
        if raw is None:
            errors.append(
                _field_error(row_no, "occurred_at", ERROR_REQUIRED_MISSING, _raw(row, column))
            )
            return _PLACEHOLDER_DATE
        parsed = _parse_iso_date(raw)
        if parsed is None:
            errors.append(_field_error(row_no, "occurred_at", ERROR_DATE_INVALID, raw))
            return _PLACEHOLDER_DATE
        return parsed

    def _validate_master_rows(
        self,
        snapshot: CsvSnapshot,
        mapping: FieldMapping,
    ) -> tuple[list[SkuDraft], list[ErrorRecord]]:
        """逐行校验主数据：SKU 草稿 + 错误记录（错误隔离，合法行不受影响）。"""
        drafts: list[SkuDraft] = []
        errors: list[ErrorRecord] = []
        for index, row in enumerate(snapshot.rows):
            row_no = index + 2  # 表头为第 1 行，数据首行从第 2 行起
            row_errors: list[ErrorRecord] = []
            draft = SkuDraft(
                row_no=row_no,
                sku_id=self._required_text(row, mapping, "sku_id", row_no, row_errors),
                name=self._required_text(row, mapping, "name", row_no, row_errors),
                category=self._optional_text(row, mapping, "category"),
                sub_category=self._optional_text(row, mapping, "sub_category"),
                unit=self._optional_text(row, mapping, "unit"),
                unit_cost=self._optional_unit_cost(row, mapping, row_no, row_errors),
                industry=self._optional_text(row, mapping, "industry"),
            )
            if row_errors:
                errors.extend(row_errors)
                continue
            drafts.append(draft)
        return drafts, errors

    def _validate_event_rows(
        self,
        snapshot: CsvSnapshot,
        mapping: FieldMapping,
    ) -> tuple[list[InventoryEventCreate], list[ErrorRecord]]:
        """逐行校验库存事件：事件草稿 + 错误记录（含 SKU/仓库存在性）。"""
        events: list[InventoryEventCreate] = []
        errors: list[ErrorRecord] = []
        for index, row in enumerate(snapshot.rows):
            row_no = index + 2
            row_errors: list[ErrorRecord] = []
            event_id = self._required_text(row, mapping, "event_id", row_no, row_errors)
            sku_id = self._required_text(row, mapping, "sku_id", row_no, row_errors)
            warehouse_id = self._required_text(
                row, mapping, "warehouse_id", row_no, row_errors
            )
            move_type = self._required_text(row, mapping, "move_type", row_no, row_errors)
            if move_type and move_type not in MOVE_TYPES:
                row_errors.append(
                    _field_error(row_no, "move_type", ERROR_MOVE_TYPE_INVALID, move_type)
                )
                move_type = ""
            quantity = self._required_quantity(row, mapping, row_no, row_errors)
            occurred_day = self._required_date(row, mapping, row_no, row_errors)
            unit_cost = self._optional_unit_cost(row, mapping, row_no, row_errors)
            source_ref = self._optional_text(row, mapping, "source_ref")
            # 存在性校验（事件导入）：外键事实保护，缺失记错误行而非放行撞库
            if sku_id and self._master.get_sku_by_sku_id(sku_id) is None:
                row_errors.append(_field_error(row_no, "sku_id", ERROR_SKU_NOT_FOUND, sku_id))
            if warehouse_id and (
                self._master.get_warehouse_by_warehouse_id(warehouse_id) is None
            ):
                row_errors.append(
                    _field_error(
                        row_no, "warehouse_id", ERROR_WAREHOUSE_NOT_FOUND, warehouse_id
                    )
                )
            if row_errors:
                errors.extend(row_errors)
                continue
            events.append(
                InventoryEventCreate(
                    event_id=event_id,
                    sku_id=sku_id,
                    warehouse_id=warehouse_id,
                    move_type=move_type,
                    quantity=quantity,
                    occurred_at=_to_utc_iso(occurred_day),
                    source=EVENT_SOURCE_CSV,
                    unit_cost=unit_cost,
                    source_ref=source_ref,
                )
            )
        return events, errors

    # ------------------------------------------------------------------
    # 内部：提交（错误隔离——库级冲突也转为错误行，不放行整批失败）
    # ------------------------------------------------------------------

    def _commit_master(
        self,
        drafts: Sequence[SkuDraft],
    ) -> tuple[int, int, list[ErrorRecord]]:
        """逐条写入 SKU（create-only）；重复 sku_id 记为错误行（M1 不覆盖）。"""
        errors: list[ErrorRecord] = []
        inserted = 0
        for draft in drafts:
            try:
                self._master.add_sku(
                    sku_id=draft.sku_id,
                    name=draft.name,
                    category=draft.category,
                    sub_category=draft.sub_category,
                    unit=draft.unit,
                    unit_cost=draft.unit_cost,
                    industry=draft.industry,
                )
            except IntegrityError:
                errors.append(
                    _field_error(draft.row_no, "sku_id", ERROR_SKU_DUPLICATE, draft.sku_id)
                )
                continue
            inserted += 1
        return inserted, 0, errors

    def _commit_events(
        self,
        events: Sequence[InventoryEventCreate],
    ) -> tuple[int, int, list[ErrorRecord]]:
        """幂等批量写入事件（重复 event_id 计入跳过）；有新事件则重建余额投影。"""
        if not events:
            return 0, 0, []
        result = self._events.upsert_events(events)
        if result.inserted > 0:
            self._projection.rebuild()
        return result.inserted, result.skipped, []
