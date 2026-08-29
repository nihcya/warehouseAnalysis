"""契约枚举：库存事件类型、事件来源、Warning 严重级与 B 侧最小错误码。

枚举值为稳定英文标识，跨端按值序列化（如 ``"OUTBOUND"``）；
UI 依据 code/枚举值判断行为，中文注释与文案仅用于展示。
"""

from enum import Enum


class MoveType(str, Enum):
    """库存事件类型；数量方向语义由公式口径（docs/formula-spec.md）冻结。"""

    INBOUND = "INBOUND"  # 入库
    OUTBOUND = "OUTBOUND"  # 出库
    RETURN = "RETURN"  # 退货（客户退回）
    SCRAP = "SCRAP"  # 报废
    TRANSFER = "TRANSFER"  # 调拨
    STOCKTAKE = "STOCKTAKE"  # 盘点
    REVERSAL = "REVERSAL"  # 冲销


class EventSource(str, Enum):
    """库存事件来源系统。"""

    IMPORT = "IMPORT"  # 历史数据导入
    DESKTOP = "DESKTOP"  # 桌面工作台录入
    MINI_PROGRAM = "MINI_PROGRAM"  # 小程序录入
    ADJUSTMENT = "ADJUSTMENT"  # 库存调整


class WarningSeverity(str, Enum):
    """Warning 严重级别。"""

    INFO = "INFO"  # 提示
    WARN = "WARN"  # 警告（通常非阻断）
    ERROR = "ERROR"  # 严重（是否阻断以 Warning.blocking 为准）


class ErrorCode(str, Enum):
    """B 侧最小错误码集合（M0 冻结；新增错误码不得复用已有值）。"""

    AUTH_REQUIRED = "AUTH_REQUIRED"  # 未登录或缺少凭证
    AUTH_FORBIDDEN = "AUTH_FORBIDDEN"  # 无权限
    LICENSE_EXPIRED = "LICENSE_EXPIRED"  # 许可证过期
    DEVICE_REVOKED = "DEVICE_REVOKED"  # 设备已被吊销
    CONFIG_VERSION_UNSUPPORTED = "CONFIG_VERSION_UNSUPPORTED"  # 配置版本不支持
    CONFIG_SIGNATURE_INVALID = "CONFIG_SIGNATURE_INVALID"  # 配置签名校验失败
    DATA_VALIDATION_FAILED = "DATA_VALIDATION_FAILED"  # 数据校验失败
    SKU_NOT_FOUND = "SKU_NOT_FOUND"  # 引用了不存在的 SKU
    INVENTORY_INSUFFICIENT = "INVENTORY_INSUFFICIENT"  # 库存不足
    DUPLICATE_EVENT = "DUPLICATE_EVENT"  # 重复库存事件
    SYNC_CURSOR_INVALID = "SYNC_CURSOR_INVALID"  # 同步游标无效
    SYNC_ENVELOPE_EXPIRED = "SYNC_ENVELOPE_EXPIRED"  # 同步信封过期
    ANALYSIS_CANCELLED = "ANALYSIS_CANCELLED"  # 分析被取消
    ANALYSIS_FAILED = "ANALYSIS_FAILED"  # 分析失败
    REPORT_RENDER_FAILED = "REPORT_RENDER_FAILED"  # 报告渲染失败
    BACKUP_VERIFY_FAILED = "BACKUP_VERIFY_FAILED"  # 备份校验失败
    MIGRATION_FAILED = "MIGRATION_FAILED"  # 数据迁移失败
    BENCHMARK_UNAVAILABLE = "BENCHMARK_UNAVAILABLE"  # 无匹配行业基准
    INTERNAL_ERROR = "INTERNAL_ERROR"  # 引擎内部错误
