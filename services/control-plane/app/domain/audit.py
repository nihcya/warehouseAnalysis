"""审计领域对象（M2）。

主基线 §30.3 与 §35.10：所有写操作生成 request_id 与审计记录，
**开发者高风险操作必记录**；删除操作生成审计记录，但日志中不保留被删除业务数据原文。

本模块只定义动作字典、结果与条目结构；字段白名单过滤在
``app/application/audit.py`` 统一收敛，数据库层不做内容校验。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class AuditAction(str, Enum):
    """受审计的动作（新增动作走「只加不改」，同步补 control_enum 种子）。"""

    AUTH_LOGIN = "AUTH_LOGIN"  # 登录
    AUTH_REFRESH = "AUTH_REFRESH"  # 刷新令牌
    AUTH_LOGOUT = "AUTH_LOGOUT"  # 注销
    DEVICE_REGISTER = "DEVICE_REGISTER"  # 设备注册
    LICENSE_STATUS_CHANGE = "LICENSE_STATUS_CHANGE"  # 许可证状态变更


class AuditResult(str, Enum):
    """动作结果。"""

    SUCCESS = "SUCCESS"  # 成功
    DENIED = "DENIED"  # 被拒绝（认证失败、权限不足、许可证限制）
    ERROR = "ERROR"  # 执行异常


#: detail_json 允许的键（字段白名单：禁止写入密码、令牌、业务明细）
ALLOWED_DETAIL_KEYS: frozenset[str] = frozenset(
    {
        "reason",  # 拒绝/失败原因码（如 REFRESH_TOKEN_REUSED、DEVICE_LIMIT_EXCEEDED）
        "client_type",  # 客户端类型
        "device_type",  # 设备类型
        "device_name",  # 设备名称（用户自定义，非业务数据）
        "license_id",  # 许可证标识
        "from_status",  # 状态迁移前
        "to_status",  # 状态迁移后
        "account_status",  # 账号状态（拒绝时说明原因，不含凭证）
        "tenant_id",  # 商户标识
    }
)


@dataclass
class AuditEntry:
    """审计条目：谁、对谁、做了什么、结果如何。"""

    audit_id: str
    action: AuditAction
    result: AuditResult
    occurred_at: datetime
    actor_account_id: str | None = None
    actor_role: str | None = None
    tenant_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    request_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
