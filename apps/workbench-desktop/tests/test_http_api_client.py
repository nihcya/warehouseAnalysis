"""HTTP API 客户端测试：用内存仓储控制平面（FastAPI ASGI）作为后端。

覆盖：
- 登录成功、令牌持久化、account/me 往返、设备注册与列表、snapshot；
- 401 自动刷新令牌重试、refresh_token 轮换、logout 清除本地令牌；
- 离线场景（base_url 指向不可达地址）时 online=False，方法返回 None；
- TokenStore 读写与清除、设备指纹稳定性。

控制平面服务目录加入 sys.path 后以 ``import app.*`` 引用（与 control-plane
自身测试同口径），演示账号经 ``CONTROL_PLANE_DEMO_PASSWORD`` 注入内存种子。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from workbench.infrastructure.api_client.device_fingerprint import (
    FINGERPRINT_LENGTH,
    generate_device_fingerprint,
    get_device_name,
)
from workbench.infrastructure.api_client.http_client import HttpApiClient
from workbench.infrastructure.api_client.token_store import TokenBundle, TokenStore

#: 仓库根（apps/workbench-desktop/tests → apps/workbench-desktop → apps → 仓库根）
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: 控制平面服务目录（加入 sys.path 使 import app.* 可用）
_SERVICE_ROOT = str(_REPO_ROOT / "services" / "control-plane")
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

#: 演示账号登录名
DEMO_MERCHANT_LOGIN = "merchant_demo"

#: 演示密码环境变量名
DEMO_PASSWORD_ENV = "CONTROL_PLANE_DEMO_PASSWORD"

#: 演示账号固定密码（经 DEMO_PASSWORD_ENV 注入内存种子）
DEMO_PASSWORD = "unit-test-demo-pass"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def control_plane_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """内存仓储 + 固定演示密码的控制平面 FastAPI 应用。"""
    # 在导入控制平面模块前设置环境变量，确保 get_settings 读到内存仓储
    monkeypatch.setenv(DEMO_PASSWORD_ENV, DEMO_PASSWORD)
    monkeypatch.setenv("CONTROL_PLANE_REPOSITORY", "memory")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_SECRET", "unit-test-secret")
    # 不可达端口让 /health 的数据库探测快速失败（不等待超时）
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/x")

    from app.container import build_container
    from app.main import create_app
    from app.settings import REPOSITORY_MEMORY, Settings, get_settings

    # 清除 lru_cache，确保读到上面设置的环境变量
    get_settings.cache_clear()

    settings = Settings(
        APP_ENV="dev",
        AUTH_SECRET="unit-test-secret",
        CONTROL_PLANE_REPOSITORY=REPOSITORY_MEMORY,
    )
    container = build_container(settings)
    return create_app(container=container)


@pytest.fixture()
def token_store(tmp_path: Path) -> TokenStore:
    """隔离的令牌存储（tmp 下子目录）。"""
    return TokenStore(data_dir=tmp_path / "auth")


@pytest.fixture()
def api_client(
    control_plane_app: Any,
    tmp_path: Path,
) -> HttpApiClient:
    """指向内存控制平面的 HttpApiClient（注入 Starlette TestClient 做 ASGI 桥接）。"""
    from fastapi.testclient import TestClient as FastAPITestClient

    store = TokenStore(data_dir=tmp_path / "auth")
    test_client = FastAPITestClient(control_plane_app)
    return HttpApiClient(
        base_url="http://testserver",
        token_store=store,
        client=test_client,
    )


@pytest.fixture()
def offline_client(tmp_path: Path) -> HttpApiClient:
    """指向不可达地址的 HttpApiClient（离线场景）。

    用 MockTransport 模拟连接被拒，避免依赖真实端口状态。
    """
    store = TokenStore(data_dir=tmp_path / "auth-offline")

    def _refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("连接被拒绝", request=request)

    mock_client = httpx.Client(
        base_url="http://unreachable.test",
        transport=httpx.MockTransport(_refuse),
    )
    return HttpApiClient(
        base_url="http://unreachable.test",
        token_store=store,
        client=mock_client,
    )


def _login(client: HttpApiClient) -> dict[str, Any]:
    """登录演示账号，断言成功并返回 AuthData。"""
    data = client.login(DEMO_MERCHANT_LOGIN, DEMO_PASSWORD)
    assert data is not None, "登录应成功"
    return data


# --------------------------------------------------------------------------
# 认证
# --------------------------------------------------------------------------


def test_login_success_returns_auth_data(api_client: HttpApiClient) -> None:
    """登录成功：返回令牌对、账号、商户与许可证；online=True。"""
    data = _login(api_client)
    assert data["tokens"]["access_token"]
    assert data["tokens"]["refresh_token"]
    assert data["tokens"]["token_type"]
    assert data["account"]["login_name"] == DEMO_MERCHANT_LOGIN
    assert data["tenant"]["tenant_id"]
    assert data["license"]["status"] == "ACTIVE"
    assert api_client.online is True


def test_login_persists_tokens(api_client: HttpApiClient, token_store: TokenStore) -> None:
    """登录后令牌持久化到 TokenStore：load 取回与响应一致。"""
    data = _login(api_client)
    # api_client 内部的 token_store 与 fixture 的 token_store 指向同一目录
    bundle = token_store.load()
    assert bundle is not None
    assert bundle.access_token == data["tokens"]["access_token"]
    assert bundle.refresh_token == data["tokens"]["refresh_token"]
    assert bundle.server_url == "http://testserver"
    assert bundle.expires_at is not None


def test_login_wrong_password_returns_none(api_client: HttpApiClient) -> None:
    """密码错误：返回 None（不抛异常），online 仍为 True（服务可达）。"""
    data = api_client.login(DEMO_MERCHANT_LOGIN, "wrong-password")
    assert data is None
    assert api_client.online is True


def test_refresh_token_rotates_and_persists(api_client: HttpApiClient) -> None:
    """refresh_token：轮换令牌对，新令牌落盘。"""
    _login(api_client)
    old_bundle = api_client._token_store.load()
    assert old_bundle is not None

    assert api_client.refresh_token() is True
    new_bundle = api_client._token_store.load()
    assert new_bundle is not None
    assert new_bundle.access_token != old_bundle.access_token
    assert new_bundle.refresh_token != old_bundle.refresh_token


def test_logout_clears_local_tokens(api_client: HttpApiClient) -> None:
    """logout：服务端注销成功，本地令牌清除。"""
    _login(api_client)
    assert api_client.logout() is True
    assert api_client._token_store.load() is None


def test_auto_refresh_on_401(api_client: HttpApiClient) -> None:
    """access_token 失效时，_request 自动刷新令牌重试一次。"""
    _login(api_client)
    bundle = api_client._token_store.load()
    assert bundle is not None

    # 篡改 access_token，保留有效 refresh_token
    api_client._token_store.save(
        TokenBundle(
            access_token="invalid-access-token",
            refresh_token=bundle.refresh_token,
            server_url=bundle.server_url,
            expires_at=bundle.expires_at,
        )
    )

    # get_account_me 应自动刷新并重试成功
    me = api_client.get_account_me()
    assert me is not None
    assert me["account"]["login_name"] == DEMO_MERCHANT_LOGIN


# --------------------------------------------------------------------------
# 账号
# --------------------------------------------------------------------------


def test_account_me_roundtrip(api_client: HttpApiClient) -> None:
    """account/me 往返：登录后取回账号、商户与许可证上下文。"""
    _login(api_client)
    me = api_client.get_account_me()
    assert me is not None
    assert me["account"]["login_name"] == DEMO_MERCHANT_LOGIN
    assert me["tenant"]["tenant_id"]
    assert me["license"]["status"]


def test_account_me_without_login_returns_none(api_client: HttpApiClient) -> None:
    """未登录时 account/me 返回 None（401 且无 refresh_token 可刷新）。"""
    assert api_client.get_account_me() is None


# --------------------------------------------------------------------------
# 设备
# --------------------------------------------------------------------------


def test_device_register_and_list_roundtrip(api_client: HttpApiClient) -> None:
    """注册设备后列表可见；同一指纹重复注册幂等。"""
    _login(api_client)
    fingerprint = generate_device_fingerprint()
    name = get_device_name()

    registered = api_client.register_device(name, fingerprint)
    assert registered is not None
    device_id = registered["device_id"]
    assert device_id
    assert registered["fingerprint"] == fingerprint

    # 重复注册幂等
    duplicate = api_client.register_device(name, fingerprint)
    assert duplicate is not None
    assert duplicate["device_id"] == device_id

    devices = api_client.list_devices()
    assert devices is not None
    assert [d["device_id"] for d in devices] == [device_id]


# --------------------------------------------------------------------------
# 状态流
# --------------------------------------------------------------------------


def test_get_snapshot(api_client: HttpApiClient) -> None:
    """snapshot：返回许可证与设备列表（轮询降级入口）。"""
    _login(api_client)
    snapshot = api_client.get_snapshot()
    assert snapshot is not None
    assert snapshot["event_id"] is not None
    assert snapshot["license"]["status"]
    assert isinstance(snapshot["devices"], list)


def test_stream_url(api_client: HttpApiClient) -> None:
    """stream_url 返回 SSE 流地址。"""
    url = api_client.stream_url()
    assert url == "http://testserver/api/v1/events/stream"


# --------------------------------------------------------------------------
# 健康检查
# --------------------------------------------------------------------------


def test_health_online(api_client: HttpApiClient) -> None:
    """在线时 /health 返回应用状态（数据库探测 down 不影响 status=ok）。"""
    health = api_client.health()
    assert health["status"] == "ok"
    assert health["app_version"]
    assert api_client.online is True


# --------------------------------------------------------------------------
# 离线场景
# --------------------------------------------------------------------------


def test_offline_login_returns_none(offline_client: HttpApiClient) -> None:
    """离线时 login 返回 None，online=False。"""
    assert offline_client.login(DEMO_MERCHANT_LOGIN, DEMO_PASSWORD) is None
    assert offline_client.online is False


def test_offline_account_me_returns_none(offline_client: HttpApiClient) -> None:
    """离线时 get_account_me 返回 None。"""
    assert offline_client.get_account_me() is None
    assert offline_client.online is False


def test_offline_health_returns_placeholder(offline_client: HttpApiClient) -> None:
    """离线时 health 返回占位信息，online=False。"""
    health = offline_client.health()
    assert health["error"]["code"] == "CLIENT_OFFLINE"
    assert offline_client.online is False


def test_offline_list_devices_returns_none(offline_client: HttpApiClient) -> None:
    """离线时 list_devices 返回 None。"""
    assert offline_client.list_devices() is None
    assert offline_client.online is False


# --------------------------------------------------------------------------
# TokenStore 读写与清除
# --------------------------------------------------------------------------


def test_token_store_save_and_load(token_store: TokenStore) -> None:
    """写入后读回字段一致。"""
    bundle = TokenBundle(
        access_token="at-123",
        refresh_token="rt-456",
        server_url="http://localhost:8000",
        expires_at="2026-12-31T00:00:00Z",
    )
    token_store.save(bundle)
    loaded = token_store.load()
    assert loaded is not None
    assert loaded.access_token == "at-123"
    assert loaded.refresh_token == "rt-456"
    assert loaded.server_url == "http://localhost:8000"
    assert loaded.expires_at == "2026-12-31T00:00:00Z"


def test_token_store_load_missing_returns_none(token_store: TokenStore) -> None:
    """文件不存在时 load 返回 None。"""
    assert token_store.load() is None


def test_token_store_clear(token_store: TokenStore) -> None:
    """清除后 load 返回 None。"""
    token_store.save(
        TokenBundle(
            access_token="at",
            refresh_token="rt",
            server_url="http://localhost:8000",
        )
    )
    assert token_store.load() is not None
    token_store.clear()
    assert token_store.load() is None


def test_token_store_clear_idempotent(token_store: TokenStore) -> None:
    """文件不存在时 clear 静默成功。"""
    token_store.clear()  # 不应抛异常


def test_token_store_creates_data_dir(tmp_path: Path) -> None:
    """save 自动创建不存在的数据目录。"""
    data_dir = tmp_path / "nested" / "auth"
    store = TokenStore(data_dir=data_dir)
    store.save(
        TokenBundle(
            access_token="at",
            refresh_token="rt",
            server_url="http://localhost:8000",
        )
    )
    assert data_dir.exists()
    assert store.load() is not None


# --------------------------------------------------------------------------
# 设备指纹稳定性
# --------------------------------------------------------------------------


def test_device_fingerprint_stable() -> None:
    """同一机器多次生成相同指纹。"""
    fp1 = generate_device_fingerprint()
    fp2 = generate_device_fingerprint()
    assert fp1 == fp2


def test_device_fingerprint_length() -> None:
    """指纹长度为 32 字符。"""
    fp = generate_device_fingerprint()
    assert len(fp) == FINGERPRINT_LENGTH


def test_device_name_nonempty() -> None:
    """设备名非空。"""
    assert get_device_name()
