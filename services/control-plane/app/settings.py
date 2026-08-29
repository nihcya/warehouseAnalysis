"""控制平面配置（pydantic-settings）：从环境变量与 .env 读取。"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS_ORIGINS 的逗号分隔列表（去空白、忽略空项）。"""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """返回进程级单例配置。"""
    return Settings()
