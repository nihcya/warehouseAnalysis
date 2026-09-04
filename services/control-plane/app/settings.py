"""控制平面配置（pydantic-settings）：从环境变量与 .env 读取。

M2 新增认证与仓储相关配置：

- ``AUTH_SECRET``：Access Token 签名密钥；**生产环境必须显式配置**，
  未配置时非生产环境使用进程内临时密钥（不写入代码、不落盘，重启即失效）；
- ``CONTROL_PLANE_REPOSITORY``：仓储实现选择（``postgres`` 生产路径 /
  ``memory`` 测试注入与本地无库演示）；
- ``LICENSE_OFFLINE_GRACE_DAYS``：许可证离线宽限天数（主基线 §10.3，默认 7 天）。

M3 新增同步与配置签名密钥：

- ``SYNC_ENCRYPTION_KEY``：小程序事件信封的 Fernet 对称加密密钥
  （``Fernet.generate_key()`` 生成，urlsafe base64）；云端加密、工作台解密，
  两端必须一致；生产环境必须显式配置；
- ``CONFIG_SIGNING_SECRET``：配置版本 HMAC-SHA256 签名密钥；客户端验签使用，
  生产环境必须显式配置。
"""

from __future__ import annotations

import secrets
from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 生产环境标识（APP_ENV 取值之一）
PRODUCTION_ENV = "production"

#: 进程内临时签名密钥：仅用于非生产环境且未显式配置 AUTH_SECRET 的场景
_EPHEMERAL_DEV_SECRET = secrets.token_urlsafe(48)

#: 进程内临时 Fernet 密钥：仅用于非生产环境且未显式配置 SYNC_ENCRYPTION_KEY 的场景
_EPHEMERAL_FERNET_KEY = Fernet.generate_key().decode("utf-8")

#: 仓储实现取值
REPOSITORY_POSTGRES = "postgres"
REPOSITORY_MEMORY = "memory"


class Settings(BaseSettings):
    """控制平面运行配置。

    字段名即环境变量名（APP_ENV / DATABASE_URL / CORS_ORIGINS）；
    仓库根目录的 ``.env`` 文件与进程环境变量均可覆盖默认值。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    #: 运行环境（dev / staging / prod）
    APP_ENV: str = "dev"
    #: 应用版本（/health.app_version 与导出 openapi.json 的 info.version 一致）
    APP_VERSION: str = "0.1.0"
    #: 云端控制库连接串（运行时健康检查与 Alembic 迁移共用同一来源）
    DATABASE_URL: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/warehouse_control"
    )
    #: 允许的 CORS 来源（逗号分隔，M0 默认本地 Web 开发地址）
    CORS_ORIGINS: str = "http://localhost:3000"
    #: Access Token 签名密钥；生产环境必须显式配置，禁止留空
    AUTH_SECRET: str = ""
    #: 仓储实现：postgres（生产路径）| memory（测试注入与本地无库演示）
    CONTROL_PLANE_REPOSITORY: str = REPOSITORY_POSTGRES
    #: 许可证离线宽限天数（过期后仍允许本地工作的天数，0 表示不设宽限）
    LICENSE_OFFLINE_GRACE_DAYS: int = 7
    #: 同步信封 Fernet 对称加密密钥；生产环境必须显式配置
    SYNC_ENCRYPTION_KEY: str = ""
    #: 配置版本 HMAC-SHA256 签名密钥；生产环境必须显式配置
    CONFIG_SIGNING_SECRET: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS_ORIGINS 的逗号分隔列表（去空白、忽略空项）。"""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        """是否为生产环境（决定是否强制要求显式密钥等安全配置）。"""
        return self.APP_ENV.strip().lower() == PRODUCTION_ENV

    def resolve_auth_secret(self) -> str:
        """返回 Access Token 签名密钥。

        显式配置优先；未配置时：生产环境直接抛错（拒绝带空密钥启动），
        其它环境使用进程内临时密钥（重启即失效，不写入代码或配置文件）。
        """
        if self.AUTH_SECRET:
            return self.AUTH_SECRET
        if self.is_production:
            raise RuntimeError(
                "生产环境必须显式配置 AUTH_SECRET（禁止使用临时密钥签发令牌）。"
            )
        return _EPHEMERAL_DEV_SECRET

    def resolve_sync_encryption_key(self) -> str:
        """返回同步信封 Fernet 加密密钥（与 resolve_auth_secret 同一口径）。"""
        if self.SYNC_ENCRYPTION_KEY:
            return self.SYNC_ENCRYPTION_KEY
        if self.is_production:
            raise RuntimeError(
                "生产环境必须显式配置 SYNC_ENCRYPTION_KEY（禁止使用临时密钥加密信封）。"
            )
        return _EPHEMERAL_FERNET_KEY

    def resolve_config_signing_secret(self) -> str:
        """返回配置版本 HMAC 签名密钥（与 resolve_auth_secret 同一口径）。"""
        if self.CONFIG_SIGNING_SECRET:
            return self.CONFIG_SIGNING_SECRET
        if self.is_production:
            raise RuntimeError(
                "生产环境必须显式配置 CONFIG_SIGNING_SECRET（禁止使用临时密钥签发配置）。"
            )
        return _EPHEMERAL_DEV_SECRET


@lru_cache
def get_settings() -> Settings:
    """返回进程级单例配置。"""
    return Settings()
