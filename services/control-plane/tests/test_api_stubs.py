"""api/v1 stub 与 Scope 依赖测试（M2 版）。

M2 变更：鉴权从 dev token（``Bearer merchant``）切换为真实 JWT，
故本文件的端点清单与取令牌方式同步调整：

- 已实装的端点（``/auth/*``、``/account/me``、``/devices*``、``/events/*``）
  移出 stub 清单，由各自的测试文件覆盖；
- 维持 stub 的端点（M3 范围）仍断言 501 + ``details.stub``，
  但现在必须先通过真实鉴权（无令牌 401、Scope 不足 403）。

覆盖：stub 恒 501（含 details.stub 标注）、无凭证 401 AUTH_REQUIRED、
商户 Scope 访问开发者接口 403 AUTH_FORBIDDEN、统一错误响应格式、
请求校验错误与未捕获异常的兜底行为。
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import TEST_PASSWORD, create_merchant, login_token
from fastapi.testclient import TestClient

#: (method, path, 通过 Scope 依赖所需 scope)
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

#: scope -> 登录账号（演示种子提供两种角色）
ACCOUNT_BY_SCOPE = {"merchant": "merchant_demo", "developer": "developer_demo"}


@pytest.fixture()
def tokens(client: TestClient) -> dict[str, str]:
    """按 scope 缓存登录令牌：scope -> access_token。"""
    return {
        scope: client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": TEST_PASSWORD},
        ).json()["data"]["tokens"]["access_token"]
        for scope, username in ACCOUNT_BY_SCOPE.items()
    }


def _request(
    client: TestClient,
    method: str,
    path: str,
    token: str | None,
) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    return client.request(method, path, headers=headers)


@pytest.mark.parametrize(("method", "path", "scope"), STUB_ENDPOINTS)
def test_stub_endpoints_501_with_valid_scope(
    client: TestClient,
    tokens: dict[str, str],
    method: str,
    path: str,
    scope: str,
) -> None:
    """携带所需 scope 的真实令牌访问 stub：统一 501。"""
    resp = _request(client, method, path, tokens[scope])
    assert resp.status_code == 501
    error = resp.json()["error"]
    assert error["code"] == "INTERNAL_ERROR"
    assert error["details"]["stub"] is True
    assert error["details"]["endpoint"] == f"{method} {path}"


@pytest.mark.parametrize(("method", "path", "scope"), STUB_ENDPOINTS)
def test_protected_endpoints_require_auth(
    client: TestClient,
    method: str,
    path: str,
    scope: str,
) -> None:
    """受保护端点无凭证：401 + AUTH_REQUIRED + request_id。"""
    resp = _request(client, method, path, None)
    assert resp.status_code == 401
    error = resp.json()["error"]
    assert error["code"] == "AUTH_REQUIRED"
    assert error["request_id"]


def test_merchant_scope_cannot_access_developer_endpoint(
    client: TestClient, tokens: dict[str, str]
) -> None:
    """商户 Scope 访问开发者接口：403 + AUTH_FORBIDDEN（不执行业务逻辑）。"""
    resp = _request(client, "GET", "/api/v1/merchants", tokens["merchant"])
    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "AUTH_FORBIDDEN"
    assert error["details"]["required_scope"] == "developer"
    assert error["request_id"]


def test_developer_scope_passes_dependency_but_stub_501(
    client: TestClient, tokens: dict[str, str]
) -> None:
    """开发者 Scope 通过依赖：接口仍未实现，返回 501。"""
    resp = _request(client, "GET", "/api/v1/merchants", tokens["developer"])
    assert resp.status_code == 501


def test_dev_token_string_is_no_longer_accepted(client: TestClient) -> None:
    """M0 的 dev token（``Bearer merchant``）已下线：按无效令牌返回 401。"""
    resp = client.get("/api/v1/devices", headers={"Authorization": "Bearer merchant"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


def test_tampered_token_returns_auth_required(client: TestClient) -> None:
    """被篡改的令牌：401 AUTH_REQUIRED（不区分签名错误与过期）。"""
    token = (
        client.post(
            "/api/v1/auth/login",
            json={"username": "merchant_demo", "password": TEST_PASSWORD},
        )
        .json()["data"]["tokens"]["access_token"]
    )
    resp = client.get("/api/v1/devices", headers={"Authorization": f"Bearer {token}x"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


def test_merchant_without_license_is_denied_on_business_endpoints(
    client: TestClient, container: Any
) -> None:
    """未开通许可证的商户：业务端点 403 LICENSE_EXPIRED（reason=LICENSE_MISSING）。"""
    account, _ = create_merchant(container, password="other-passw0rd", with_license=False)
    other_token = login_token(client, account.login_name, "other-passw0rd")

    resp = client.get("/api/v1/devices", headers={"Authorization": f"Bearer {other_token}"})
    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "LICENSE_EXPIRED"
    assert error["details"]["reason"] == "LICENSE_MISSING"


def test_error_envelope_shape(client: TestClient) -> None:
    """错误响应统一为 {"error": {code, message, details, request_id}}。"""
    resp = _request(client, "GET", "/api/v1/devices", None)
    assert resp.status_code == 401
    body = resp.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details", "request_id"}


def test_request_id_header_present(client: TestClient) -> None:
    """每个请求都带 X-Request-ID 响应头。"""
    resp = _request(client, "GET", "/api/v1/config", None)
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


def test_unexpected_error_returns_sanitized_500() -> None:
    """未捕获异常：500 + INTERNAL_ERROR，不泄露堆栈与异常信息。"""
    from app.main import create_app
    from conftest import build_test_container

    app = create_app(build_test_container())

    @app.get("/api/v1/_boom")
    def _boom() -> None:
        raise RuntimeError("boom: sensitive detail")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/_boom")
    assert resp.status_code == 500
    error = resp.json()["error"]
    assert error["code"] == "INTERNAL_ERROR"
    assert "boom" not in error["message"]
    assert "sensitive" not in str(error["details"])
    assert error["request_id"]
