"""api/v1 契约测试：M2 实装端点（认证/账号/设备）+ M3 维持 stub 的端点。

覆盖：
- 登录成功/失败、令牌刷新轮换与重放撤销、注销后访问受保护端点 401；
- 受 stub 端点恒 501（含 details.stub 标注），无凭证 401，商户 Scope 访问
  开发者接口 403；
- 统一错误响应格式、请求校验错误与未捕获异常的兜底行为。

M0 的 dev token（``Bearer merchant``）已随 M2 下线：测试通过内存仓储种子账号
（merchant_demo / developer_demo）走真实登录换取令牌。
"""

from __future__ import annotations

from typing import Any

import pytest
from app.container import build_container
from app.infrastructure.memory.seed import (
    DEMO_DEVELOPER_LOGIN,
    DEMO_MERCHANT_LOGIN,
    DEMO_PASSWORD_ENV,
)
from app.main import create_app
from app.settings import REPOSITORY_MEMORY, Settings
from fastapi.testclient import TestClient

#: 演示账号固定密码（经 DEMO_PASSWORD_ENV 注入内存种子，不写死在服务代码里）
DEMO_PASSWORD = "unit-test-demo-pass"

#: M3 维持 stub 的端点：(method, path, 访问所需 scope)
STUB_ENDPOINTS: list[tuple[str, str, str]] = [
    ("GET", "/api/v1/config", "merchant"),
    ("GET", "/api/v1/tasks", "merchant"),
    ("POST", "/api/v1/tasks/pull", "merchant"),
    ("GET", "/api/v1/sync/events/pull", "merchant"),
    ("POST", "/api/v1/sync/ack", "merchant"),
    ("GET", "/api/v1/telemetry", "developer"),
    ("GET", "/api/v1/merchants", "developer"),
    ("POST", "/api/v1/heartbeat", "merchant"),
]


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """内存仓储 + 固定演示密码的控制平面测试客户端（不依赖 PostgreSQL）。"""
    monkeypatch.setenv(DEMO_PASSWORD_ENV, DEMO_PASSWORD)
    settings = Settings(
        APP_ENV="dev",
        AUTH_SECRET="unit-test-secret",
        CONTROL_PLANE_REPOSITORY=REPOSITORY_MEMORY,
    )
    container = build_container(settings)
    return TestClient(create_app(container=container))


def _login(client: TestClient, login_name: str = DEMO_MERCHANT_LOGIN) -> dict[str, Any]:
    """走真实登录换取令牌对，返回响应 data 载荷。"""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": login_name, "password": DEMO_PASSWORD},
    )
    assert resp.status_code == 200
    return resp.json()["data"]


def _auth_header(data: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {data['tokens']['access_token']}"}


# --------------------------------------------------------------------------
# 认证（M2 实装）
# --------------------------------------------------------------------------


def test_login_success_returns_tokens_and_context(client: TestClient) -> None:
    """商户演示账号登录：返回令牌对、账号、商户与许可证评估。"""
    data = _login(client)
    assert data["tokens"]["access_token"]
    assert data["tokens"]["refresh_token"]
    assert data["tokens"]["token_type"]
    assert data["account"]["login_name"] == DEMO_MERCHANT_LOGIN
    assert data["tenant"]["tenant_id"]
    assert data["license"]["status"] == "ACTIVE"


def test_login_wrong_password_returns_401(client: TestClient) -> None:
    """密码错误：401 + AUTH_REQUIRED + request_id（不泄露账号是否存在）。"""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": DEMO_MERCHANT_LOGIN, "password": "wrong-password"},
    )
    assert resp.status_code == 401
    error = resp.json()["error"]
    assert error["code"] == "AUTH_REQUIRED"
    assert error["request_id"]


def test_login_unknown_account_returns_401(client: TestClient) -> None:
    """账号不存在：与密码错误同口径 401（避免账号枚举）。"""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "no-such-user", "password": DEMO_PASSWORD},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


def test_refresh_rotates_tokens_and_replay_revokes(client: TestClient) -> None:
    """刷新轮换令牌；旧 Refresh Token 重放 → 401（重放检测撤销会话）。"""
    data = _login(client)
    old_refresh = data["tokens"]["refresh_token"]

    refreshed = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": old_refresh},
    )
    assert refreshed.status_code == 200
    new_tokens = refreshed.json()["data"]["tokens"]
    assert new_tokens["refresh_token"]
    assert new_tokens["refresh_token"] != old_refresh

    replay = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": old_refresh},
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "AUTH_REQUIRED"


def test_logout_revokes_session(client: TestClient) -> None:
    """注销后原 Access Token 访问受保护端点 401；重复注销幂等 200。"""
    data = _login(client)
    header = _auth_header(data)

    logout = client.post("/api/v1/auth/logout", headers=header)
    assert logout.status_code == 200
    assert logout.json()["data"]["revoked"] is True

    me = client.get("/api/v1/account/me", headers=header)
    assert me.status_code == 401
    assert me.json()["error"]["code"] == "AUTH_REQUIRED"

    # 令牌本身仍有效（未过期），重复注销保持幂等成功
    again = client.post("/api/v1/auth/logout", headers=header)
    assert again.status_code == 200


def test_account_me_returns_context(client: TestClient) -> None:
    """account/me：账号 + 商户 + 许可证（许可证过期不影响该端点）。"""
    data = _login(client)
    resp = client.get("/api/v1/account/me", headers=_auth_header(data))
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["account"]["login_name"] == DEMO_MERCHANT_LOGIN
    assert payload["tenant"]["tenant_id"]
    assert payload["license"]["status"]


# --------------------------------------------------------------------------
# 设备（M2 实装）
# --------------------------------------------------------------------------


def test_device_register_and_list_roundtrip(client: TestClient) -> None:
    """注册设备后列表可见；同一指纹重复注册幂等。"""
    header = _auth_header(_login(client))
    register = client.post(
        "/api/v1/devices/register",
        json={"name": "测试工作台", "fingerprint": "fp-unit-test-001"},
        headers=header,
    )
    assert register.status_code == 200
    device_id = register.json()["data"]["device_id"]
    assert device_id

    duplicate = client.post(
        "/api/v1/devices/register",
        json={"name": "测试工作台", "fingerprint": "fp-unit-test-001"},
        headers=header,
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["data"]["device_id"] == device_id

    listing = client.get("/api/v1/devices", headers=header)
    assert listing.status_code == 200
    devices = listing.json()["data"]
    assert [device["device_id"] for device in devices] == [device_id]


def test_device_endpoints_require_auth(client: TestClient) -> None:
    """设备接口无凭证：401 + AUTH_REQUIRED。"""
    resp = client.get("/api/v1/devices")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


# --------------------------------------------------------------------------
# stub 端点（M3 交付）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "scope"), STUB_ENDPOINTS)
def test_stub_endpoints_501_with_valid_scope(
    client: TestClient,
    method: str,
    path: str,
    scope: str,
) -> None:
    """携带所需 scope 的有效令牌访问 stub：统一 501 + details 标注。"""
    login_name = DEMO_DEVELOPER_LOGIN if scope == "developer" else DEMO_MERCHANT_LOGIN
    header = _auth_header(_login(client, login_name))
    resp = client.request(method, path, headers=header)
    assert resp.status_code == 501
    error = resp.json()["error"]
    assert error["code"] == "INTERNAL_ERROR"
    assert error["details"]["stub"] is True
    assert error["details"]["endpoint"] == f"{method} {path}"


@pytest.mark.parametrize(("method", "path", "scope"), STUB_ENDPOINTS)
def test_stub_endpoints_require_auth(
    client: TestClient,
    method: str,
    path: str,
    scope: str,
) -> None:
    """受保护 stub 端点无凭证：401 + AUTH_REQUIRED + request_id。"""
    resp = client.request(method, path)
    assert resp.status_code == 401
    error = resp.json()["error"]
    assert error["code"] == "AUTH_REQUIRED"
    assert error["request_id"]


def test_merchant_scope_cannot_access_developer_endpoint(client: TestClient) -> None:
    """商户 Scope 访问开发者接口：403 + AUTH_FORBIDDEN（不执行业务逻辑）。"""
    header = _auth_header(_login(client, DEMO_MERCHANT_LOGIN))
    resp = client.get("/api/v1/merchants", headers=header)
    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "AUTH_FORBIDDEN"
    assert error["details"]["required_scope"] == "developer"
    assert error["request_id"]


# --------------------------------------------------------------------------
# 统一错误格式
# --------------------------------------------------------------------------


def test_error_envelope_shape(client: TestClient) -> None:
    """错误响应统一为 {"error": {code, message, details, request_id}}。"""
    resp = client.get("/api/v1/devices")
    assert resp.status_code == 401
    body = resp.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details", "request_id"}


def test_request_id_header_present(client: TestClient) -> None:
    """每个请求都带 X-Request-ID 响应头。"""
    resp = client.get("/api/v1/config")
    assert resp.status_code == 401
    assert resp.headers.get("X-Request-ID")


def test_validation_error_returns_data_validation_failed(client: TestClient) -> None:
    """请求体缺字段：统一 400 + DATA_VALIDATION_FAILED（不使用 FastAPI 默认 422）。"""
    resp = client.post("/api/v1/auth/login", json={"username": "dev"})
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "DATA_VALIDATION_FAILED"
    assert error["request_id"]
    assert error["details"]


def test_unexpected_error_returns_sanitized_500(client: TestClient) -> None:
    """未捕获异常：500 + INTERNAL_ERROR，不泄露堆栈与异常信息。"""
    container = build_container(
        Settings(
            APP_ENV="dev",
            AUTH_SECRET="unit-test-secret",
            CONTROL_PLANE_REPOSITORY=REPOSITORY_MEMORY,
        )
    )
    application = create_app(container=container)

    @application.get("/api/v1/_boom")
    def _boom() -> None:
        raise RuntimeError("boom: sensitive detail")

    boom_client = TestClient(application, raise_server_exceptions=False)
    resp = boom_client.get("/api/v1/_boom")
    assert resp.status_code == 500
    error = resp.json()["error"]
    assert error["code"] == "INTERNAL_ERROR"
    assert "boom" not in error["message"]
    assert "sensitive" not in str(error["details"])
    assert error["request_id"]
