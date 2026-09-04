"""配置版本（M3 实装）。

- 配置按版本发布：``(tenant_id, version)`` 唯一，**版本不可覆盖**（主基线 §35.6）；
- 发布状态机：``DRAFT -> PUBLISHED -> RETIRED``，生效配置为 PUBLISHED 中
  ``effective_at`` 最新的一条；
- 内容摘要：``sha256`` 为规范化 JSON 的 SHA-256，客户端应用前必须先验摘要与签名
  （spec：验签失败保留旧配置并告警）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ConfigStatus(str, Enum):
    """配置版本状态机（只加不改；新增取值同步迁移 CHECK 与 control_enum 种子）。"""

    DRAFT = "DRAFT"  # 草稿（未发布，不下发）
    PUBLISHED = "PUBLISHED"  # 已发布（可下发）
    RETIRED = "RETIRED"  # 已退役（历史版本保留，不再生效）


#: 允许的状态迁移表（源状态 -> 可达状态集合）
CONFIG_TRANSITIONS: dict[ConfigStatus, frozenset[ConfigStatus]] = {
    ConfigStatus.DRAFT: frozenset({ConfigStatus.PUBLISHED}),
    ConfigStatus.PUBLISHED: frozenset({ConfigStatus.RETIRED}),
    ConfigStatus.RETIRED: frozenset(),
}


@dataclass
class ConfigVersion:
    """配置版本实体：内容 + 摘要 + 签名，版本一旦发布不可覆盖。"""

    config_version_id: str
    tenant_id: str
    version: str
    content: dict[str, Any] = field(default_factory=dict)
    sha256: str = ""
    signature: str = ""
    schema_version: int = 1
    status: ConfigStatus = ConfigStatus.DRAFT
    published_by: str | None = None
    effective_at: datetime | None = None
    created_at: datetime | None = None

    def transition(self, target: ConfigStatus) -> None:
        """按状态机迁移；非法迁移抛 ValueError。"""
        if target not in CONFIG_TRANSITIONS[self.status]:
            raise ValueError(f"非法状态迁移：{self.status.value} -> {target.value}")
        self.status = target


def canonical_config_bytes(content: dict[str, Any]) -> bytes:
    """规范化序列化配置内容：键排序 + 紧凑分隔符，摘要与验签的唯一口径。"""
    return json.dumps(
        content, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def content_sha256(content: dict[str, Any]) -> str:
    """配置内容的 SHA-256 摘要（hexdigest，64 个小写十六进制字符）。"""
    return hashlib.sha256(canonical_config_bytes(content)).hexdigest()
