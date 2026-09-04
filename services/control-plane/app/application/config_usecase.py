"""配置发布与读取用例（M3 实装）。

- ``get_effective``：返回商户生效配置（PUBLISHED 中 effective_at 最新），
  无已发布配置返回 None（客户端走本地缓存兜底）；
- ``publish``：计算内容 SHA-256 摘要与 HMAC-SHA256 签名后写入新版本
  （版本不可覆盖：``(tenant_id, version)`` 重复由仓储唯一约束拦截）；
- ``verify``：客户端（工作台）应用配置前必须验签——摘要不符或签名不符
  一律拒绝应用并保留旧配置（spec：配置下发与验签）。
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from app.application.ports import ConfigRepository
from app.domain.config import ConfigStatus, ConfigVersion, content_sha256
from app.infrastructure.ids import PREFIX_CONFIG_VERSION, new_id


def config_signature(secret: str, version: str, sha256: str) -> str:
    """配置签名：HMAC-SHA256(secret, "version:sha256")。

    签名对象是版本号 + 内容摘要（而非内容原文），与摘要校验分离：
    摘要保证内容未被篡改，签名保证版本与摘要来自云端。
    """
    message = f"{version}:{sha256}".encode()
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_config(config: ConfigVersion, *, secret: str) -> bool:
    """校验配置摘要与签名；任一不符返回 False（客户端不得应用）。"""
    if content_sha256(config.content) != config.sha256:
        return False
    expected = config_signature(secret, config.version, config.sha256)
    return hmac.compare_digest(expected, config.signature)


class ConfigService:
    """配置版本应用服务。"""

    def __init__(self, configs: ConfigRepository, signing_secret: str) -> None:
        self._configs = configs
        self._signing_secret = signing_secret

    def get_effective(self, tenant_id: str) -> ConfigVersion | None:
        """返回商户生效配置；空租户（开发者上下文）返回 None。"""
        if not tenant_id:
            return None
        return self._configs.get_effective_config(tenant_id)

    def publish(
        self,
        *,
        tenant_id: str,
        version: str,
        content: dict[str, object],
        published_by: str | None = None,
        effective_at: datetime | None = None,
        now: datetime | None = None,
    ) -> ConfigVersion:
        """计算摘要与签名并写入 PUBLISHED 新版本（dev 种子与发布入口共用）。"""
        now = now or datetime.now(UTC)
        sha256 = content_sha256(content)
        config = ConfigVersion(
            config_version_id=new_id(PREFIX_CONFIG_VERSION),
            tenant_id=tenant_id,
            version=version,
            content=dict(content),
            sha256=sha256,
            signature=config_signature(self._signing_secret, version, sha256),
            status=ConfigStatus.PUBLISHED,
            published_by=published_by,
            effective_at=effective_at or now,
            created_at=now,
        )
        self._configs.add_config(config)
        return config
