"""/health 健康检查：应用状态、版本与控制库可达性。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.schemas import HealthResponse
from app.infrastructure.db.engine import check_database_reachable
from app.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="健康检查")
def health() -> HealthResponse:
    """返回 200 与应用版本、数据库可达状态。

    数据库不可达时仍返回 200（status 保持 ok），database 字段如实为 ``down``；
    路由为同步函数（探测在线程池执行，不阻塞事件循环）。
    """
    settings = get_settings()
    database = "up" if check_database_reachable(settings.DATABASE_URL) else "down"
    return HealthResponse(
        status="ok",
        app_version=settings.APP_VERSION,
        database=database,
    )
