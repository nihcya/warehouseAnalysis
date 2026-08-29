"""api/v1 stub 与 Scope 依赖测试（M0）。

覆盖：stub 恒 501（含 details.stub 标注）、无凭证 401 AUTH_REQUIRED、
商户 Scope 访问开发者接口 403 AUTH_FORBIDDEN、统一错误响应格式、
请求校验错误与未捕获异常的兜底行为。
"""

from __future__ import annotations

from typing import Any

import pytest
from app.main import create_app
from fastapi.testclient import TestClient

MERCHANT_HEADER = {"Authorization": "Bearer merchant"}
DEVELOPER_HEADER = {"Authorization": "Bearer developer"}

#: (method, path, 通过 Scope 依赖所需 scope；None 表示 M0 无 Scope 依赖)
STUB_ENDPOINTS: list[tuple[str, str, str | None]] = [
    ("POST", "/api/v1/auth/login", None),
    ("POST", "/api/v1/auth/refresh", None),
    ("POST", "/api/v1/auth/logout", None),
    ("POST", "/api/v1/devices/register", None),
    ("GET", "/api/v1/devices", "merchant"),
    ("GET", "/api/v1/config", "merchant"),
    ("GET", "/api/v1/tasks", "merchant"),
    ("POST", "/api/v1/tasks/pull", "merchant"),
    ("GET", "/api/v1/sync/events/pull", "merchant"),
    ("POST", "/api/v1/sync/ack", "merchant"),
    ("GET", "/api/v1/telemetry", "developer"),
    ("GET", "/api/v1/merchants", "developer"),
    ("POST", "/api/v1/heartbeat", "merchant"),
]

#: 带 body 的 stub 端点（其余端点无请求体）
REQUEST_BODIES: dict[str, dict[str, str]] = {
    "/api/v1/auth/login": {"username": "dev", "password": "secret"},
    "/api/v1/auth/refresh": {"refresh_token": "stub-refresh-token"},
}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _request(
    client: TestClient,
    method: str,
    path: str,
    headers: dict[str, str] | None,
) -> Any:
    body = REQUEST_BODIES.get(path)
    return client.request(method, path, headers=headers, json=body)


def test_login_stub_returns_501(client: TestClient) -> None:
    """登录 stub：501 + INTERNAL_ERROR + details 标注 stub 与端点。"""
    resp = _request(client, "POST", "/api/v1/auth/login", None)
    assert resp.status_code == 501
    error = resp.json()["error"]
    assert error["code"] == "INTERNAL_ERROR"
    assert error["message"]
    assert error["details"] == {"stub": True, "endpoint": "POST /api/v1/auth/login"}
    assert error["request_id"]


@pytest.mark.parametrize(("method", "path", "scope"), STUB_ENDPOINTS)
def test_stub_endpoints_501_with_valid_scope(
    client: TestClient,
    method: str,
    path: str,
    scope: str | None,
) -> None:
    """携带所需 scope（或无需 scope）访问 stub：统一 501。"""
    headers = {"Authorization": f"Bearer {scope}"} if scope else None
    resp = _request(client, method, path, headers)
    assert resp.status_code == 501
    error = resp.json()["error"]
    assert error["code"] == "INTERNAL_ERROR"
    assert error["details"]["stub"] is True
    assert error["details"]["endpoint"] == f"{method} {path}"


@pytest.mark.parametrize(
    ("method", "path", "scope"),
    [endpoint for endpoint in STUB_ENDPOINTS if endpoint[2] is not None],
)
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


def test_merchant_scope_cannot_access_developer_endpoint(client: TestClient) -> None:
    """商户 Scope 访问开发者接口：403 + AUTH_FORBIDDEN（不执行业务逻辑）。"""
    resp = _request(client, "GET", "/api/v1/merchants", MERCHANT_HEADER)
    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "AUTH_FORBIDDEN"
    assert error["details"]["required_scope"] == "developer"
    assert error["request_id"]


def test_developer_scope_passes_dependency_but_stub_501(client: TestClient) -> None:
    """开发者 Scope 通过依赖：接口仍未实现，返回 501。"""
    resp = _request(client, "GET", "/api/v1/merchants", DEVELOPER_HEADER)
    assert resp.status_code == 501


def test_error_envelope_shape(client: TestClient) -> None:
    """错误响应统一为 {"error": {code, message, details, request_id}}。"""
    resp = _request(client, "GET", "/api/v1/devices", None)
    assert resp.status_code == 401
    body = resp.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details", "request_id"}


def test_request_id_header_present(client: TestClient) -> None:
    """每个请求都带 X-Request-ID 响应头。"""
    resp = _request(client, "POST", "/api/v1/auth/logout", None)
    assert resp.status_code == 501
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

    app = create_app()

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
