"""审计服务（M2）。

主基线 §30.3 / §35.10：所有写操作生成 request_id 与审计记录，
开发者高风险操作必记录；日志中不保留密码、令牌与被删除业务数据原文。

本服务是审计的**唯一写入入口**：``detail`` 只接受 ``domain.audit.ALLOWED_DETAIL_KEYS``
白名单内的键，越界键直接丢弃（不抛错，避免审计拖垮业务主流程），
保证审计表不会成为敏感数据的旁路。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.application.ports import AuditRepository
from app.domain.audit import ALLOWED_DETAIL_KEYS, AuditAction, AuditEntry, AuditResult
from app.infrastructure.ids import PREFIX_AUDIT, new_id


class AuditService:
    """审计记录服务：字段白名单过滤 + 只追加写入。"""

    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    def record(
        self,
        *,
        action: AuditAction,
        result: AuditResult,
        occurred_at: datetime | None = None,
        actor_account_id: str | None = None,
        actor_role: str | None = None,
        tenant_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        request_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> AuditEntry:
        """写入一条审计记录，返回已过滤后的条目（便于测试断言）。"""
        entry = AuditEntry(
            audit_id=new_id(PREFIX_AUDIT),
            action=action,
            result=result,
            occurred_at=occurred_at or datetime.now(UTC),
            actor_account_id=actor_account_id,
            actor_role=actor_role,
            tenant_id=tenant_id,
            target_type=target_type,
            target_id=target_id,
            request_id=request_id,
            detail=_whitelist(detail),
        )
        self._repository.add(entry)
        return entry


def _whitelist(detail: dict[str, Any] | None) -> dict[str, Any]:
    """按白名单过滤 detail：只保留受控键，且值必须是标量（防止嵌套业务数据）。"""
    if not detail:
        return {}
    filtered: dict[str, Any] = {}
    for key, value in detail.items():
        if key not in ALLOWED_DETAIL_KEYS:
            continue
        if value is None or isinstance(value, (str, int, float, bool)):
            filtered[key] = value
    return filtered
