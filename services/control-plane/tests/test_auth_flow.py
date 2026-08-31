"""认证闭环测试（M2）：登录、失败锁定、刷新轮换与重放、注销、当前账号。

对应 spec：账号登录与令牌发放 / 刷新令牌轮换与重放检测 / 注销 / Scope 与租户隔离。
负向用例是硬性要求（主基线 §25.2：认证、授权必须有负向测试）。
"""

from __future__ import annotations

from typing import Any

from conftest import (
    DEMO_DEVELOPER_LOGIN,
    DEMO_MERCHANT_LOGIN,
    TEST_PASSWORD,
    create_merchant,
    login_token,
)
from fastapi.testclient import TestClient


def test_login_success_returns_tokens_and_context(client: TestClient) -> None:
    """登录成功：200 + data 信封 + 令牌对 + 账号/商户/许可证。"""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": DEMO_MERCHANT_LOGIN, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]

    tokens = data["tokens"]
    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == 900  # SECURITY.md：Access 15 分钟
    assert tokens["access_token"] and tokens["refresh_token"]

    account = data["account"]
    assert account["login_name"] == DEMO_MERCHANT_LOGIN
    assert account["role"] == "MERCHANT_OWNER"
    assert "password" not in account and "password_hash" not in account

    assert data["tenant"]["tenant_id"] == "tnt_demo"
    assert data["license"]["status"] == "ACTIVE"
    assert data["license"]["max_devices"] == 3


def test_login_wrong_password_returns_auth_required(client: TestClient) -> None:
    """密码错误：401 AUTH_REQUIRED，且不区分账号是否存在。"""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": DEMO_MERCHANT_LOGIN, "password": "wrong-password"},
    )
    assert resp.status_code == 401
    error = resp.json()["error"]
    assert error["code"] == "AUTH_REQUIRED"
    assert error["request_id"]


def test_login_unknown_account_has_same_message(client: TestClient) -> None:
    """账号不存在与密码错误文案一致（避免通过错误信息枚举账号）。"""
    wrong_password = client.post(
        "/api/v1/auth/login",
        json={"username": DEMO_MERCHANT_LOGIN, "password": "nope"},
    ).json()["error"]
    unknown = client.post(
        "/api/v1/auth/login",
        json={"username": "ghost_user", "password": "nope"},
    ).json()["error"]
    assert wrong_password["message"] == unknown["message"]
    assert wrong_password["code"] == unknown["code"]


def test_login_locks_account_after_repeated_failures(client: TestClient) -> None:
    """连续 5 次失败后账号锁定：第 6 次返回 403 + account_status=LOCKED。"""
    for _ in range(5):
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"username": DEMO_MERCHANT_LOGIN, "password": "bad"},
            ).status_code
            == 401
        )

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": DEMO_MERCHANT_LOGIN, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "AUTH_FORBIDDEN"
    assert error["details"]["account_status"] == "LOCKED"


def test_successful_login_resets_failure_counter(
    client: TestClient, container: Any
) -> None:
    """登录成功清零失败计数（后续两次失败不会立刻再次锁定）。"""
    account, _ = create_merchant(container, password="reset-passw0rd")
    for _ in range(4):
        client.post(
            "/api/v1/auth/login",
            json={"username": account.login_name, "password": "bad"},
        )
    assert client.post(
        "/api/v1/auth/login",
        json={"username": account.login_name, "password": "reset-passw0rd"},
    ).status_code == 200

    stored = container.identity.get_account_by_login_name(account.login_name)
    assert stored is not None
    assert stored.failed_attempts == 0
    assert stored.last_login_at is not None


def test_refresh_rotates_token_pair(client: TestClient) -> None:
    """刷新轮换：返回全新的 Access / Refresh 令牌对。"""
    tokens = client.post(
        "/api/v1/auth/login",
        json={"username": DEMO_MERCHANT_LOGIN, "password": TEST_PASSWORD},
    ).json()["data"]["tokens"]

    refreshed = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    new_tokens = refreshed.json()["data"]["tokens"]
    # jti 保证同一秒内重复签发也得到不同令牌
    assert new_tokens["access_token"] != tokens["access_token"]
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # 新 Refresh Token 可继续使用（轮换链正常）
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
        ).status_code
        == 200
    )


def test_refresh_replay_is_detected_and_revokes_sessions(client: TestClient) -> None:
    """重放检测：刚被轮换掉的 Refresh Token 再使用 → 401 + 撤销全部会话。

    检测窗口为"上一次轮换"（previous 指纹），符合 SECURITY.md 的轮换语义：
    令牌一旦被替换，旧令牌再出现即视为泄露。
    """
    first = client.post(
        "/api/v1/auth/login",
        json={"username": DEMO_MERCHANT_LOGIN, "password": TEST_PASSWORD},
    ).json()["data"]["tokens"]
    second = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    ).json()["data"]["tokens"]

    replay = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert replay.status_code == 401
    error = replay.json()["error"]
    assert error["code"] == "AUTH_REQUIRED"
    assert error["details"]["reason"] == "REFRESH_TOKEN_REUSED"

    # 重放触发后该账号全部会话被撤销：轮换得到的新令牌同样失效
    again = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
    )
    assert again.status_code == 401


def test_refresh_with_unknown_token_returns_auth_required(client: TestClient) -> None:
    """未知刷新令牌：401 AUTH_REQUIRED。"""
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": "not-a-token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


def test_refresh_token_is_not_stored_in_plain_text(
    client: TestClient, container: Any
) -> None:
    """Refresh Token 只以指纹持久化：库里查不到明文（主基线 §35.5）。"""
    tokens = client.post(
        "/api/v1/auth/login",
        json={"username": DEMO_MERCHANT_LOGIN, "password": TEST_PASSWORD},
    ).json()["data"]["tokens"]
    refresh_token = tokens["refresh_token"]

    store = container.identity.store
    plain_hits = [
        session
        for session in store.sessions.values()
        if refresh_token in (session.refresh_token_hash, str(session.previous_refresh_token_hash))
    ]
    assert plain_hits == []
    assert any(
        session.refresh_token_hash and len(session.refresh_token_hash) == 64
        for session in store.sessions.values()
    )


def test_logout_revokes_session_and_is_idempotent(client: TestClient) -> None:
    """注销：200；Access Token 随即失效；重复注销仍 200（幂等）。"""
    token = login_token(client, DEMO_MERCHANT_LOGIN)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/v1/account/me", headers=headers).status_code == 200

    logout = client.post("/api/v1/auth/logout", headers=headers)
    assert logout.status_code == 200
    assert logout.json()["data"]["revoked"] is True

    # 会话已撤销：即使 Access Token 未过期也不能继续使用
    assert client.get("/api/v1/account/me", headers=headers).status_code == 401
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200


def test_logout_requires_authentication(client: TestClient) -> None:
    """未携带令牌调用注销：401 AUTH_REQUIRED。"""
    assert client.post("/api/v1/auth/logout").status_code == 401


def test_me_returns_merchant_context(client: TestClient) -> None:
    """/account/me：商户账号返回商户与许可证上下文。"""
    headers = {"Authorization": f"Bearer {login_token(client, DEMO_MERCHANT_LOGIN)}"}
    resp = client.get("/api/v1/account/me", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["account"]["role"] == "MERCHANT_OWNER"
    assert data["tenant"]["tenant_id"] == "tnt_demo"
    assert data["license"]["status"] == "ACTIVE"
    assert "inventory-kpi" in data["license"]["features"]


def test_me_returns_developer_context_without_tenant(client: TestClient) -> None:
    """/account/me：开发者账号没有商户，许可证为 MISSING（不参与放行判定）。"""
    headers = {"Authorization": f"Bearer {login_token(client, DEMO_DEVELOPER_LOGIN)}"}
    resp = client.get("/api/v1/account/me", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["account"]["role"] == "DEVELOPER"
    assert data["tenant"] is None
    assert data["license"]["status"] == "MISSING"


def test_tenant_isolation_between_merchants(client: TestClient, container: Any) -> None:
    """租户隔离：两个商户各自看到自己的 tenant_id，不串号。"""
    account, tenant_id = create_merchant(container, password="iso-passw0rd")
    headers = {"Authorization": f"Bearer {login_token(client, account.login_name, 'iso-passw0rd')}"}

    mine = client.get("/api/v1/account/me", headers=headers).json()["data"]
    assert mine["tenant"]["tenant_id"] == tenant_id

    demo = client.get(
        "/api/v1/account/me",
        headers={"Authorization": f"Bearer {login_token(client, DEMO_MERCHANT_LOGIN)}"},
    ).json()["data"]
    assert demo["tenant"]["tenant_id"] == "tnt_demo"
    assert mine["account"]["account_id"] != demo["account"]["account_id"]


def test_login_records_device_id_and_client_type(
    client: TestClient, container: Any
) -> None:
    """登录带上 device_id 与 client_type：会话按声明落库。"""
    resp = client.post(
        "/api/v1/auth/login",
        json={
            "username": DEMO_MERCHANT_LOGIN,
            "password": TEST_PASSWORD,
            "client_type": "DESKTOP",
            "device_id": "dev_manual_001",
        },
    )
    assert resp.status_code == 200
    sessions = list(container.identity.store.sessions.values())
    assert len(sessions) == 1
    assert sessions[0].client_type.value == "DESKTOP"
    assert sessions[0].device_id == "dev_manual_001"


def test_unknown_client_type_falls_back_to_web(
    client: TestClient, container: Any
) -> None:
    """未知 client_type 回退 WEB，不阻断登录（契约未扩展前的兼容口径）。"""
    assert (
        client.post(
            "/api/v1/auth/login",
            json={
                "username": DEMO_MERCHANT_LOGIN,
                "password": TEST_PASSWORD,
                "client_type": "PDA",
            },
        ).status_code
        == 200
    )
    sessions = list(container.identity.store.sessions.values())
    assert sessions[0].client_type.value == "WEB"
