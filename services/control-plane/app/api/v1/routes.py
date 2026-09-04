"""api/v1 路由（M2：认证、账号、设备与状态流；M3：心跳、配置、任务与同步）。

M2 实装：

- ``POST /auth/login``、``POST /auth/refresh``、``POST /auth/logout``：认证闭环；
- ``GET /account/me``：当前账号、商户与许可证（含离线宽限期）；
- ``POST /devices/register``、``GET /devices``：设备注册与列表（受许可证设备数约束）；
- ``GET /events/stream``：SSE 状态流（Last-Event-ID 续传 + 15 秒保活）；
- ``GET /events/snapshot``：轮询降级入口（DECISIONS.md D-006：失败后 30 秒轮询）。

M3 实装：

- ``POST /heartbeat``：设备心跳（版本三元组 + 待同步数 + 运行状态，upsert 最新投影）；
- ``GET /config``：商户生效配置（版本 + 内容 + SHA-256 摘要与签名，客户端验签后应用）；
- ``GET /tasks`` / ``POST /tasks/pull``：任务定义列表与设备拉取（CREATED → QUEUED 锁定）；
- ``GET /sync/events/pull`` / ``POST /sync/ack``：加密信封拉取与幂等确认（TTL 过期清理）；
- ``POST /dev/sync/inject``：Mock 小程序事件注入（dev 工具，生产环境 403 拒绝）。

维持 stub（随开发者端页面交付）：``/telemetry``、``/merchants``。
stub 端点同样挂真实鉴权依赖，未携带有效令牌时先返回 401/403，不会落到业务逻辑。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any, NoReturn

from contracts import ErrorCode
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from app.api.v1.deps import (
    get_auth_service,
    get_config_service,
    get_container,
    get_device_service,
    get_heartbeat_service,
    get_hub,
    get_request_id,
    get_sync_service,
    get_task_service,
    require_developer_scope,
    require_principal,
    require_tenant_access,
    require_tenant_with_license,
    require_valid_token,
)
from app.api.v1.errors import ApiError, not_implemented
from app.api.v1.schemas import (
    AccountData,
    AccountMeData,
    AccountMeResponse,
    AuthData,
    ConfigData,
    ConfigResponse,
    DeviceData,
    DeviceListResponse,
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    ErrorResponse,
    HeartbeatData,
    HeartbeatRequest,
    HeartbeatResponse,
    LicenseData,
    LoginRequest,
    LoginResponse,
    LogoutData,
    LogoutResponse,
    PulledTaskData,
    RefreshRequest,
    RefreshResponse,
    SnapshotData,
    SnapshotResponse,
    SyncAckData,
    SyncAckRequest,
    SyncAckResponse,
    SyncEnvelopeData,
    SyncEventsPullResponse,
    SyncInjectRequest,
    SyncInjectResponse,
    TaskData,
    TaskListResponse,
    TaskPullRequest,
    TaskPullResponse,
    TaskRunData,
    TenantData,
    TokenData,
)
from app.application.auth_usecase import AuthResult
from app.container import Container
from app.domain.account import Account
from app.domain.config import ConfigVersion
from app.domain.device import Device, DeviceType
from app.domain.heartbeat import Heartbeat
from app.domain.license import LicenseEntitlement
from app.domain.sync import SyncEnvelope
from app.domain.task import Task, TaskRun
from app.domain.tenant import Tenant
from app.infrastructure.auth.tokens import Principal, new_client_type_of
from app.infrastructure.realtime.hub import RealtimeHub, StatusEvent, parse_last_event_id

router = APIRouter()

#: SSE 保活间隔（秒）：无事件时发送注释行，防止中间层断开连接
SSE_KEEPALIVE_SECONDS = 15
#: SSE 生成器轮询订阅队列的间隔（秒）
SSE_POLL_INTERVAL = 0.5
#: SSE 首帧快照的事件名
SSE_EVENT_SNAPSHOT = "snapshot"
#: SSE 保活帧（注释行，客户端忽略；用于穿透中间层空闲超时）
SSE_KEEPALIVE_FRAME = ": keepalive\n\n"


def _stub_responses(*, scoped: bool = False) -> dict[int | str, dict[str, Any]]:
    """生成 stub 路由的 OpenAPI 响应声明：统一 501；受保护端点另加 401/403。"""
    responses: dict[int | str, dict[str, Any]] = {
        501: {"model": ErrorResponse, "description": "M0 未实现（stub）"},
    }
    if scoped:
        responses[401] = {"model": ErrorResponse, "description": "缺少或无效的凭证"}
        responses[403] = {"model": ErrorResponse, "description": "缺少所需 Scope"}
    return responses


def _raise_stub(endpoint: str) -> NoReturn:
    """抛出统一 501 未实现错误。"""
    raise not_implemented(endpoint)


# --------------------------------------------------------------------------
# 认证
# --------------------------------------------------------------------------


@router.post(
    "/auth/login",
    tags=["auth"],
    response_model=LoginResponse,
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    summary="账号登录",
)
async def login(request: Request, body: LoginRequest) -> LoginResponse:
    """校验账号密码并签发令牌对（Access 15 分钟 / Refresh 可轮换）。"""
    result = get_auth_service(request).login(
        login_name=body.username,
        password=body.password,
        client_type=new_client_type_of(body.client_type),
        device_id=body.device_id,
        request_id=get_request_id(request),
    )
    return LoginResponse(data=_auth_data(result))


@router.post(
    "/auth/refresh",
    tags=["auth"],
    response_model=RefreshResponse,
    responses={401: {"model": ErrorResponse}},
    summary="刷新访问令牌",
)
async def refresh(request: Request, body: RefreshRequest) -> RefreshResponse:
    """轮换 Refresh Token 并重签 Access Token；重放即撤销该账号全部会话。"""
    result = get_auth_service(request).refresh(
        refresh_token=body.refresh_token,
        request_id=get_request_id(request),
    )
    return RefreshResponse(data=_auth_data(result))


@router.post(
    "/auth/logout",
    tags=["auth"],
    response_model=LogoutResponse,
    responses={401: {"model": ErrorResponse}},
    summary="退出登录",
)
async def logout(
    request: Request,
    principal: Annotated[Principal, Depends(require_valid_token)],
) -> LogoutResponse:
    """撤销当前会话；重复注销保持幂等成功（已撤销的会话也返回 200）。"""
    get_auth_service(request).logout(
        principal=principal,
        request_id=get_request_id(request),
    )
    return LogoutResponse(data=LogoutData(session_id=principal.session_id, revoked=True))


@router.get(
    "/account/me",
    tags=["account"],
    response_model=AccountMeResponse,
    responses={401: {"model": ErrorResponse}},
    summary="当前账号上下文",
)
async def read_me(
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> AccountMeResponse:
    """返回当前账号、商户与许可证评估结果（含离线宽限期状态）。"""
    context = get_auth_service(request).current_context(principal=principal)
    return AccountMeResponse(
        data=AccountMeData(
            account=_account_data(context.account),
            tenant=_tenant_data(context.tenant),
            license=_license_data(context.entitlement),
        )
    )


# --------------------------------------------------------------------------
# 设备
# --------------------------------------------------------------------------


@router.post(
    "/devices/register",
    tags=["devices"],
    response_model=DeviceRegisterResponse,
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    summary="设备注册",
)
async def register_device(
    request: Request,
    body: DeviceRegisterRequest,
    principal: Annotated[Principal, Depends(require_tenant_with_license)],
) -> DeviceRegisterResponse:
    """注册设备：同一指纹幂等，已吊销拒绝，超出许可证设备数上限拒绝。"""
    device = get_device_service(request).register(
        tenant_id=principal.tenant_id or "",
        account_id=principal.account_id,
        actor_role=principal.role.value,
        device_type=_device_type_of(body.device_type),
        name=body.name,
        fingerprint=body.fingerprint,
        app_version=body.app_version,
        request_id=get_request_id(request),
    )
    return DeviceRegisterResponse(data=_device_data(device))


@router.get(
    "/devices",
    tags=["devices"],
    response_model=DeviceListResponse,
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    summary="设备列表",
)
async def list_devices(
    request: Request,
    principal: Annotated[Principal, Depends(require_tenant_with_license)],
) -> DeviceListResponse:
    """列出当前商户的全部设备（数据范围由令牌 tenant_id 决定）。"""
    devices = get_device_service(request).list_devices(principal.tenant_id or "")
    return DeviceListResponse(data=[_device_data(device) for device in devices])


# --------------------------------------------------------------------------
# 状态流（SSE + 轮询降级）
# --------------------------------------------------------------------------


@router.get(
    "/events/stream",
    tags=["events"],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    summary="状态流（SSE）",
)
async def stream_events(
    request: Request,
    principal: Annotated[Principal, Depends(require_tenant_access)],
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    last_event_id_query: str | None = Query(default=None, alias="last_event_id"),
) -> StreamingResponse:
    """SSE 状态流：首帧快照 → 断线续传补发 → 实时事件 + 15 秒保活。

    客户端断线重连时由浏览器自动带上 ``Last-Event-ID``；首次连接可用查询参数
    ``last_event_id`` 指定起点。连接失败时应降级为 30 秒轮询
    ``GET /events/snapshot``（DECISIONS.md D-006）。
    """
    return StreamingResponse(
        sse_event_stream(
            get_hub(request),
            get_container(request),
            principal.tenant_id,
            resume_from=parse_last_event_id(last_event_id or last_event_id_query),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 30 秒轮询降级：客户端据此切换到快照轮询
            "X-Polling-Fallback": "/api/v1/events/snapshot",
            "X-Polling-Interval": "30",
        },
    )


@router.get(
    "/events/snapshot",
    tags=["events"],
    response_model=SnapshotResponse,
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    summary="状态快照（轮询降级）",
)
async def events_snapshot(
    request: Request,
    principal: Annotated[Principal, Depends(require_tenant_access)],
) -> SnapshotResponse:
    """轮询降级入口：SSE 不可用时客户端每 30 秒拉一次当前状态。"""
    return SnapshotResponse(data=build_snapshot(get_container(request), principal.tenant_id))


# --------------------------------------------------------------------------
# 心跳（M3）
# --------------------------------------------------------------------------


@router.post(
    "/heartbeat",
    tags=["heartbeat"],
    response_model=HeartbeatResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    summary="设备心跳上报",
)
async def heartbeat(
    request: Request,
    body: HeartbeatRequest,
    principal: Annotated[Principal, Depends(require_tenant_access)],
) -> HeartbeatResponse:
    """工作台 Agent 心跳：upsert 最新投影并刷新设备在线状态。

    设备必须已注册且归属当前商户（数据范围由令牌 tenant_id 决定，
    不信任前端传入的任何租户字段）。
    """
    stored = get_heartbeat_service(request).report(
        tenant_id=principal.tenant_id or "",
        device_id=body.device_id,
        status=body.status,
        app_version=body.app_version,
        engine_version=body.engine_version,
        db_schema_version=body.db_schema_version,
        pending_sync_count=body.pending_sync_count,
    )
    return HeartbeatResponse(data=_heartbeat_data(stored))


# --------------------------------------------------------------------------
# 配置（M3）
# --------------------------------------------------------------------------


@router.get(
    "/config",
    tags=["config"],
    response_model=ConfigResponse,
    responses={401: {"model": ErrorResponse}},
    summary="拉取生效配置",
)
async def get_config(
    request: Request,
    principal: Annotated[Principal, Depends(require_tenant_access)],
) -> ConfigResponse:
    """返回商户生效配置（版本 + 内容 + SHA-256 摘要与签名）。

    客户端必须先校验摘要与签名再应用（spec：验签失败保留旧配置并告警）；
    无已发布配置时 ``data`` 为 null，客户端走本地缓存兜底。
    """
    config = get_config_service(request).get_effective(principal.tenant_id or "")
    return ConfigResponse(data=_config_data(config) if config is not None else None)


# --------------------------------------------------------------------------
# 任务（M3）
# --------------------------------------------------------------------------


@router.get(
    "/tasks",
    tags=["tasks"],
    response_model=TaskListResponse,
    responses={401: {"model": ErrorResponse}},
    summary="任务列表",
)
async def list_tasks(
    request: Request,
    principal: Annotated[Principal, Depends(require_tenant_access)],
) -> TaskListResponse:
    """列出商户全部任务定义（含禁用的，UI 需明示启用状态）。"""
    tasks = get_task_service(request).list_tasks(principal.tenant_id or "")
    return TaskListResponse(data=[_task_data(task) for task in tasks])


@router.post(
    "/tasks/pull",
    tags=["tasks"],
    response_model=TaskPullResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    summary="拉取待执行任务",
)
async def pull_tasks(
    request: Request,
    body: TaskPullRequest,
    principal: Annotated[Principal, Depends(require_tenant_access)],
) -> TaskPullResponse:
    """设备拉取待执行任务：CREATED 运行原子锁定为 QUEUED，锁定后不再重复分发。

    任务由云端定义、本地执行：完整结果留在本地，客户端执行后按 run_id 幂等上报状态。
    """
    pairs = get_task_service(request).pull(
        tenant_id=principal.tenant_id or "",
        device_id=body.device_id,
        limit=body.limit,
    )
    return TaskPullResponse(
        data=[PulledTaskData(task=_task_data(task), run=_run_data(run)) for task, run in pairs]
    )


# --------------------------------------------------------------------------
# 小程序事件同步（M3）
# --------------------------------------------------------------------------


@router.get(
    "/sync/events/pull",
    tags=["sync"],
    response_model=SyncEventsPullResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    summary="拉取同步事件信封",
)
async def pull_sync_events(
    request: Request,
    principal: Annotated[Principal, Depends(require_tenant_access)],
    device_id: Annotated[str, Query(description="拉取信封的目标设备标识")],
    limit: Annotated[
        int, Query(ge=1, le=200, description="单次拉取上限，默认 50")
    ] = 50,
) -> SyncEventsPullResponse:
    """按设备拉取 ENQUEUED 加密信封；拉取时顺带清理 TTL 过期信封。

    云端只见密文；工作台解密、校验并落库成功后再 ACK。断网重试期间
    信封保持 ENQUEUED（不伪造成功）。
    """
    envelopes = get_sync_service(request).pull(
        tenant_id=principal.tenant_id or "",
        device_id=device_id,
        limit=limit,
    )
    return SyncEventsPullResponse(data=[_envelope_data(envelope) for envelope in envelopes])


@router.post(
    "/sync/ack",
    tags=["sync"],
    response_model=SyncAckResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    summary="确认同步事件",
)
async def ack_sync(
    request: Request,
    body: SyncAckRequest,
    principal: Annotated[Principal, Depends(require_tenant_access)],
) -> SyncAckResponse:
    """确认信封已应用：本地事务成功后逐个确认，重复 ACK 幂等成功。"""
    _, already_acked = get_sync_service(request).ack(
        tenant_id=principal.tenant_id or "",
        envelope_id=body.envelope_id,
    )
    return SyncAckResponse(
        data=SyncAckData(envelope_id=body.envelope_id, already_acked=already_acked)
    )


@router.post(
    "/dev/sync/inject",
    tags=["sync"],
    response_model=SyncInjectResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse},
               403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    summary="Mock 小程序事件注入（dev 工具）",
)
async def inject_sync_event(
    request: Request,
    body: SyncInjectRequest,
    principal: Annotated[Principal, Depends(require_tenant_access)],
) -> SyncInjectResponse:
    """dev 工具：Mock 小程序事件加密为密文信封写入 sync_envelope。

    仅供本地联调与端到端测试使用；生产环境一律 403 拒绝
    （不区分是否携带有效凭证，避免成为生产注入入口）。
    """
    if get_container(request).settings.is_production:
        raise ApiError(
            code=ErrorCode.AUTH_FORBIDDEN,
            message="生产环境禁用 Mock 事件注入端点。",
            details={"endpoint": "POST /api/v1/dev/sync/inject"},
            status_code=403,
        )
    envelope = get_sync_service(request).inject(
        tenant_id=principal.tenant_id or "",
        target_device_id=body.target_device_id,
        payload=body.payload,
        event_id=body.event_id,
        idempotency_key=body.idempotency_key,
        ttl_seconds=body.ttl_seconds,
    )
    return SyncInjectResponse(data=_envelope_data(envelope))


# --------------------------------------------------------------------------
# 维持 stub（随开发者端页面交付）
# --------------------------------------------------------------------------


@router.get(
    "/telemetry",
    tags=["telemetry"],
    responses=_stub_responses(scoped=True),
    summary="技术日志查询（开发者，stub）",
)
async def get_telemetry(principal: Annotated[Principal, Depends(require_developer_scope)]) -> None:
    """开发者按商户 / 设备 / 版本 / 错误码筛选技术日志（stub：随开发者端交付）。"""
    _raise_stub("GET /api/v1/telemetry")


@router.get(
    "/merchants",
    tags=["merchants"],
    responses=_stub_responses(scoped=True),
    summary="商户列表（开发者，stub）",
)
async def list_merchants(principal: Annotated[Principal, Depends(require_developer_scope)]) -> None:
    """开发者查询商户列表（stub：随开发者端页面交付）。"""
    _raise_stub("GET /api/v1/merchants")


# --------------------------------------------------------------------------
# 序列化辅助
# --------------------------------------------------------------------------


def _auth_data(result: AuthResult) -> AuthData:
    """认证结果 → 响应载荷（不含任何内部字段）。"""
    return AuthData(
        tokens=TokenData(
            access_token=result.tokens.access_token,
            refresh_token=result.tokens.refresh_token,
            token_type=result.tokens.token_type,
            expires_in=result.tokens.expires_in,
            refresh_expires_at=result.tokens.refresh_expires_at,
        ),
        account=_account_data(result.context.account),
        tenant=_tenant_data(result.context.tenant),
        license=_license_data(result.context.entitlement),
    )


def _account_data(account: Account) -> AccountData:
    return AccountData(
        account_id=account.account_id,
        login_name=account.login_name,
        role=account.role.value,
        tenant_id=account.tenant_id,
        status=account.status.value,
        last_login_at=account.last_login_at,
    )


def _tenant_data(tenant: Tenant | None) -> TenantData | None:
    if tenant is None:
        return None
    return TenantData(
        tenant_id=tenant.tenant_id,
        name=tenant.name,
        status=tenant.status.value,
        product_profile_id=tenant.product_profile_id,
    )


def _license_data(entitlement: LicenseEntitlement) -> LicenseData:
    return LicenseData(
        status=entitlement.status.value,
        license_id=entitlement.license_id,
        product_profile_code=entitlement.product_profile_code,
        starts_at=entitlement.starts_at,
        expires_at=entitlement.expires_at,
        grace_days=entitlement.grace_days,
        grace_ends_at=entitlement.grace_ends_at,
        days_remaining=entitlement.days_remaining,
        max_devices=entitlement.max_devices,
        features=list(entitlement.features),
    )


def _device_data(device: Device) -> DeviceData:
    return DeviceData(
        device_id=device.device_id,
        tenant_id=device.tenant_id,
        name=device.name,
        device_type=device.device_type.value,
        fingerprint=device.fingerprint,
        status=device.status.value,
        app_version=device.app_version,
        last_seen_at=device.last_seen_at,
        registered_at=device.registered_at,
    )


def _device_type_of(value: str) -> DeviceType:
    """解析设备类型；未知取值回退 DESKTOP（工作台是第一客户端）。"""
    try:
        return DeviceType(value)
    except ValueError:
        return DeviceType.DESKTOP


def _heartbeat_data(heartbeat: Heartbeat) -> HeartbeatData:
    return HeartbeatData(
        device_id=heartbeat.device_id,
        tenant_id=heartbeat.tenant_id,
        sent_at=heartbeat.sent_at,
        status=heartbeat.status,
        app_version=heartbeat.app_version,
        engine_version=heartbeat.engine_version,
        db_schema_version=heartbeat.db_schema_version,
        pending_sync_count=heartbeat.pending_sync_count,
    )


def _config_data(config: ConfigVersion) -> ConfigData:
    return ConfigData(
        tenant_id=config.tenant_id,
        version=config.version,
        content=dict(config.content),
        sha256=config.sha256,
        signature=config.signature,
        schema_version=config.schema_version,
        status=config.status.value,
        effective_at=config.effective_at,
        created_at=config.created_at,
    )


def _task_data(task: Task) -> TaskData:
    return TaskData(
        task_id=task.task_id,
        tenant_id=task.tenant_id,
        task_type=task.task_type,
        cron_expr=task.cron_expr,
        scope=dict(task.scope),
        enabled=task.enabled,
        created_at=task.created_at,
    )


def _run_data(run: TaskRun) -> TaskRunData:
    return TaskRunData(
        run_id=run.run_id,
        task_id=run.task_id,
        tenant_id=run.tenant_id,
        status=run.status.value,
        device_id=run.device_id,
        scheduled_at=run.scheduled_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error_code=run.error_code,
    )


def _envelope_data(envelope: SyncEnvelope) -> SyncEnvelopeData:
    return SyncEnvelopeData(
        envelope_id=envelope.envelope_id,
        event_id=envelope.event_id,
        target_device_id=envelope.target_device_id,
        ciphertext=envelope.ciphertext,
        idempotency_key=envelope.idempotency_key,
        status=envelope.status.value,
        expires_at=envelope.expires_at,
        created_at=envelope.created_at,
    )


async def sse_event_stream(
    hub: RealtimeHub,
    container: Container,
    tenant_id: str | None,
    *,
    resume_from: int | None = None,
    keepalive_seconds: float = SSE_KEEPALIVE_SECONDS,
    poll_interval: float = SSE_POLL_INTERVAL,
) -> AsyncIterator[str]:
    """生成 SSE 帧序列：续传补发 → 首帧快照 → 实时事件 + 保活。

    抽成模块级生成器（而非路由内闭包）是为了可测试：
    ``httpx`` 的 ASGITransport 与 Starlette TestClient 都会把流缓冲到响应结束，
    无法增量读取永不结束的 SSE 流，故单测直接驱动本生成器逐帧断言。
    """
    with hub.subscribe() as subscriber:
        for event in hub.events_since(resume_from):
            if _visible(event, tenant_id):
                yield _sse_frame(event)

        snapshot = build_snapshot(container, tenant_id)
        yield _sse_frame(
            StatusEvent(
                event_id=hub.latest_event_id,
                event_type=SSE_EVENT_SNAPSHOT,
                payload=snapshot.model_dump(mode="json"),
                occurred_at=snapshot.generated_at,
                tenant_id=tenant_id,
            )
        )

        last_alive = time.monotonic()
        while True:
            pending = hub.drain(subscriber.queue)
            for event in pending:
                if _visible(event, tenant_id):
                    yield _sse_frame(event)
            if pending:
                last_alive = time.monotonic()
                continue
            if time.monotonic() - last_alive >= keepalive_seconds:
                yield SSE_KEEPALIVE_FRAME
                last_alive = time.monotonic()
            await asyncio.sleep(poll_interval)


def build_snapshot(container: Container, tenant_id: str | None) -> SnapshotData:
    """构造状态快照：SSE 首帧与轮询降级共用同一结构，保证两条通道语义一致。"""
    entitlement = container.entitlement_service.evaluate(tenant_id)
    devices = container.device_service.list_devices(tenant_id) if tenant_id else []
    # 待同步数：各设备最新心跳投影之和（M3 心跳上报后可计算；无心跳投影返回 None）
    pending_counts = [
        heartbeat.pending_sync_count
        for device in devices
        if (heartbeat := container.heartbeats.get(device.device_id)) is not None
    ]
    return SnapshotData(
        event_id=container.hub.latest_event_id,
        generated_at=datetime.now(UTC),
        license=_license_data(entitlement),
        devices=[_device_data(device) for device in devices],
        pending_sync_count=sum(pending_counts) if pending_counts else None,
        tasks=[],  # 任务运行投影片段随开发者端页面交付（仓储暂无按租户列运行的端口）
    )


def _visible(event: StatusEvent, tenant_id: str | None) -> bool:
    """事件是否对该租户可见（租户隔离：只推送本商户事件）。"""
    return event.tenant_id is None or event.tenant_id == tenant_id


def _sse_frame(event: StatusEvent) -> str:
    """按 SSE 规范渲染一帧：``id`` / ``event`` / ``data`` 三行 + 空行结束。"""
    payload = json.dumps(event.payload, ensure_ascii=False)
    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {payload}\n\n"
