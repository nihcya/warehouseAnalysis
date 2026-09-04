"""M3 端点契约测试：心跳、配置、任务与小程序事件同步。

覆盖（spec deliver-m3-agent-sync-release Task 2）：
- ``POST /heartbeat``：upsert 最新投影、设备转 ONLINE、未知/跨租户设备 400；
- ``GET /config``：种子演示配置，摘要与签名可按客户端口径复算验证；
  无已发布配置的商户返回 ``data=null``（客户端走本地缓存）；
- ``GET /tasks`` / ``POST /tasks/pull``：任务列表、拉取锁定（CREATED→QUEUED）
  且锁定后不再重复分发；
- ``GET /sync/events/pull`` / ``POST /sync/ack``：注入 → 拉取 → 解密 → 确认
  幂等 → 不再下发；TTL 过期清理；重复 event_id 409；跨租户隔离；
- ``POST /dev/sync/inject``：生产环境 403 拒绝；
- ``GET /events/snapshot``：待同步数聚合自各设备最新心跳投影。

全部走内存仓储 + 真实登录（与 test_api_stubs.py 同一套路）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.application.config_usecase import config_signature
from app.container import Container, build_container
from app.domain.account import Account, AccountRole
from app.domain.license import License, LicenseStatus
from app.domain.tenant import Tenant
from app.infrastructure.auth.passwords import hash_password
from app.infrastructure.memory.seed import (
    DEMO_CONFIG_CONTENT,
    DEMO_CONFIG_VERSION,
    DEMO_MERCHANT_LOGIN,
    DEMO_PASSWORD_ENV,
    DEMO_PRODUCT_PROFILE_ID,
)
from app.infrastructure.sync_crypto import decrypt_json
from app.main import create_app
from app.settings import REPOSITORY_MEMORY, Settings
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

#: 演示账号固定密码（经 DEMO_PASSWORD_ENV 注入内存种子）
DEMO_PASSWORD = "unit-test-demo-pass"

#: 固定签名密钥：种子配置签名与客户端复算共用同一口径
TEST_SIGNING_SECRET = "unit-test-config-signing-secret"

#: 固定 Fernet 密钥：注入加密与测试解密共用同一口径
TEST_FERNET_KEY = Fernet.generate_key().decode("utf-8")

#: M3 受保护端点（无凭证一律 401）
PROTECTED_ENDPOINTS: list[tuple[str, str]] = [
    ("POST", "/api/v1/heartbeat"),
    ("GET", "/api/v1/config"),
    ("GET", "/api/v1/tasks"),
    ("POST", "/api/v1/tasks/pull"),
    ("GET", "/api/v1/sync/events/pull?device_id=dev_any"),
    ("POST", "/api/v1/sync/ack"),
    ("POST", "/api/v1/dev/sync/inject"),
]


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """内存仓储 + 固定密钥的控制平面测试客户端（不依赖 PostgreSQL）。"""
    monkeypatch.setenv(DEMO_PASSWORD_ENV, DEMO_PASSWORD)
    settings = Settings(
        APP_ENV="dev",
        AUTH_SECRET="unit-test-secret",
        CONTROL_PLANE_REPOSITORY=REPOSITORY_MEMORY,
        CONFIG_SIGNING_SECRET=TEST_SIGNING_SECRET,
        SYNC_ENCRYPTION_KEY=TEST_FERNET_KEY,
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


def _container(client: TestClient) -> Container:
    """取测试客户端背后的依赖容器（``TestClient.app`` 需收窄为 FastAPI 才有 ``state``）。"""
    assert isinstance(client.app, FastAPI)
    container: Container = client.app.state.container
    return container


def _register_device(
    client: TestClient,
    header: dict[str, str],
    fingerprint: str = "fp-m3-test-001",
) -> str:
    """注册一台工作台设备并返回 device_id（心跳/拉取的前置条件）。"""
    resp = client.post(
        "/api/v1/devices/register",
        json={"name": "M3 测试设备", "fingerprint": fingerprint},
        headers=header,
    )
    assert resp.status_code == 200
    return resp.json()["data"]["device_id"]


def _add_tenant_with_license(
    client: TestClient,
    tenant_id: str,
    login_name: str,
) -> dict[str, str]:
    """直接在仓储中追加一个带许可证的商户与账号，返回其认证头（租户隔离测试）。"""
    container = _container(client)
    today = datetime.now(UTC).date()
    container.identity.add_tenant(Tenant(tenant_id=tenant_id, name=f"商户-{tenant_id}"))
    container.entitlements.add_license(
        License(
            license_id=f"lic_{tenant_id}",
            tenant_id=tenant_id,
            product_profile_id=DEMO_PRODUCT_PROFILE_ID,
            starts_at=today,
            expires_at=today + timedelta(days=30),
            max_devices=3,
            status=LicenseStatus.ACTIVE,
        )
    )
    container.identity.add_account(
        Account(
            account_id=f"acc_{login_name}",
            login_name=login_name,
            password_hash=hash_password(DEMO_PASSWORD),
            role=AccountRole.MERCHANT_OWNER,
            tenant_id=tenant_id,
        )
    )
    return _auth_header(_login(client, login_name))


# --------------------------------------------------------------------------
# 鉴权
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), PROTECTED_ENDPOINTS)
def test_m3_endpoints_require_auth(client: TestClient, method: str, path: str) -> None:
    """M3 端点无凭证：401 + AUTH_REQUIRED。"""
    resp = client.request(method, path)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


# --------------------------------------------------------------------------
# 心跳
# --------------------------------------------------------------------------


def test_heartbeat_reports_and_marks_device_online(client: TestClient) -> None:
    """心跳成功：返回最新投影，设备状态 REGISTERED → ONLINE。"""
    header = _auth_header(_login(client))
    device_id = _register_device(client, header)

    resp = client.post(
        "/api/v1/heartbeat",
        json={
            "device_id": device_id,
            "status": "RUNNING",
            "app_version": "0.3.0",
            "engine_version": "1.2.0",
            "db_schema_version": "local-0007",
            "pending_sync_count": 2,
        },
        headers=header,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["device_id"] == device_id
    assert data["status"] == "RUNNING"
    assert data["app_version"] == "0.3.0"
    assert data["engine_version"] == "1.2.0"
    assert data["db_schema_version"] == "local-0007"
    assert data["pending_sync_count"] == 2
    assert data["sent_at"]

    devices = client.get("/api/v1/devices", headers=header).json()["data"]
    device = next(item for item in devices if item["device_id"] == device_id)
    assert device["status"] == "ONLINE"
    assert device["last_seen_at"]


def test_heartbeat_upserts_latest_projection(client: TestClient) -> None:
    """重复心跳按 device_id 覆盖为最新投影（一行一台设备）。"""
    header = _auth_header(_login(client))
    device_id = _register_device(client, header, fingerprint="fp-m3-upsert")

    first = client.post(
        "/api/v1/heartbeat",
        json={"device_id": device_id, "status": "RUNNING", "pending_sync_count": 1},
        headers=header,
    )
    assert first.status_code == 200

    second = client.post(
        "/api/v1/heartbeat",
        json={"device_id": device_id, "status": "IDLE", "pending_sync_count": 0},
        headers=header,
    )
    assert second.status_code == 200
    data = second.json()["data"]
    assert data["status"] == "IDLE"
    assert data["pending_sync_count"] == 0


def test_heartbeat_unknown_device_returns_400(client: TestClient) -> None:
    """设备不存在（或不属于当前商户）：400 + DATA_VALIDATION_FAILED。"""
    header = _auth_header(_login(client))
    resp = client.post(
        "/api/v1/heartbeat",
        json={"device_id": "dev_not_exists", "status": "RUNNING"},
        headers=header,
    )
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "DATA_VALIDATION_FAILED"
    assert error["details"]["reason"] == "DEVICE_NOT_FOUND"


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------


def test_config_returns_seeded_and_verifiable_signature(client: TestClient) -> None:
    """返回种子演示配置；摘要与签名可按客户端口径复算验证。"""
    from app.domain.config import content_sha256

    header = _auth_header(_login(client))
    resp = client.get("/api/v1/config", headers=header)
    assert resp.status_code == 200
    config = resp.json()["data"]
    assert config["version"] == DEMO_CONFIG_VERSION
    assert config["status"] == "PUBLISHED"
    assert config["content"] == DEMO_CONFIG_CONTENT
    # 客户端验签口径（spec：验签失败拒绝应用并保留旧配置）
    assert content_sha256(config["content"]) == config["sha256"]
    expected = config_signature(TEST_SIGNING_SECRET, config["version"], config["sha256"])
    assert config["signature"] == expected


def test_config_null_when_tenant_has_no_published_config(client: TestClient) -> None:
    """无已发布配置的商户：200 且 data=null（客户端走本地缓存兜底）。"""
    header = _add_tenant_with_license(client, "tnt_no_config", "merchant_no_config")
    resp = client.get("/api/v1/config", headers=header)
    assert resp.status_code == 200
    assert resp.json()["data"] is None


# --------------------------------------------------------------------------
# 任务
# --------------------------------------------------------------------------


def test_tasks_list_seeded_definitions(client: TestClient) -> None:
    """任务列表：返回种子任务定义（含 cron 与执行范围）。"""
    header = _auth_header(_login(client))
    resp = client.get("/api/v1/tasks", headers=header)
    assert resp.status_code == 200
    tasks = resp.json()["data"]
    assert [task["task_type"] for task in tasks] == [
        "ANALYSIS_INVENTORY_KPI",
        "ANALYSIS_ABC_AGING",
    ]
    assert tasks[0]["cron_expr"] == "0 8 * * *"
    assert tasks[0]["scope"] == {"feature": "inventory-kpi"}
    assert all(task["enabled"] for task in tasks)


def test_tasks_pull_locks_and_does_not_redispatch(client: TestClient) -> None:
    """拉取待执行任务：CREATED → QUEUED 锁定，锁定后不再重复分发。"""
    header = _auth_header(_login(client))
    device_id = _register_device(client, header, fingerprint="fp-m3-pull")

    first = client.post(
        "/api/v1/tasks/pull",
        json={"device_id": device_id},
        headers=header,
    )
    assert first.status_code == 200
    items = first.json()["data"]
    assert len(items) == 1
    task, run = items[0]["task"], items[0]["run"]
    assert task["task_type"] == "ANALYSIS_INVENTORY_KPI"
    assert run["status"] == "QUEUED"
    assert run["device_id"] == device_id

    second = client.post(
        "/api/v1/tasks/pull",
        json={"device_id": device_id},
        headers=header,
    )
    assert second.status_code == 200
    assert second.json()["data"] == []


def test_tasks_pull_unknown_device_returns_400(client: TestClient) -> None:
    """拉取任务的设备不存在：400 + DEVICE_NOT_FOUND。"""
    header = _auth_header(_login(client))
    resp = client.post(
        "/api/v1/tasks/pull",
        json={"device_id": "dev_not_exists"},
        headers=header,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["details"]["reason"] == "DEVICE_NOT_FOUND"


# --------------------------------------------------------------------------
# 小程序事件同步（注入 → 拉取 → 解密 → ACK）
# --------------------------------------------------------------------------


def test_sync_inject_pull_ack_flow(client: TestClient) -> None:
    """同步闭环：注入密文信封 → 拉取 → 工作台口径解密 → ACK 幂等 → 不再下发。"""
    header = _auth_header(_login(client))
    device_id = _register_device(client, header, fingerprint="fp-m3-sync")
    payload = {"event_type": "INVENTORY_CHANGE", "sku_id": "SKU-001", "delta": -2}

    injected = client.post(
        "/api/v1/dev/sync/inject",
        json={
            "target_device_id": device_id,
            "payload": payload,
            "idempotency_key": "wx-req-0001",
        },
        headers=header,
    )
    assert injected.status_code == 200
    envelope = injected.json()["data"]
    assert envelope["status"] == "ENQUEUED"
    assert envelope["event_id"].startswith("evt_")
    assert envelope["ciphertext"]
    assert envelope["idempotency_key"] == "wx-req-0001"
    assert envelope["expires_at"]

    pulled = client.get(
        f"/api/v1/sync/events/pull?device_id={device_id}",
        headers=header,
    )
    assert pulled.status_code == 200
    envelopes = pulled.json()["data"]
    assert [item["envelope_id"] for item in envelopes] == [envelope["envelope_id"]]

    # 工作台口径：同一 Fernet 密钥可解密回事件原文
    key = _container(client).settings.resolve_sync_encryption_key()
    assert decrypt_json(envelopes[0]["ciphertext"], key) == payload

    ack = client.post(
        "/api/v1/sync/ack",
        json={"envelope_id": envelope["envelope_id"]},
        headers=header,
    )
    assert ack.status_code == 200
    assert ack.json()["data"] == {
        "envelope_id": envelope["envelope_id"],
        "already_acked": False,
    }

    # 重复 ACK 幂等成功（spec：幂等重放）
    repeat = client.post(
        "/api/v1/sync/ack",
        json={"envelope_id": envelope["envelope_id"]},
        headers=header,
    )
    assert repeat.status_code == 200
    assert repeat.json()["data"]["already_acked"] is True

    # ACK 后不再下发
    again = client.get(f"/api/v1/sync/events/pull?device_id={device_id}", headers=header)
    assert again.status_code == 200
    assert again.json()["data"] == []


def test_sync_ack_unknown_envelope_returns_400(client: TestClient) -> None:
    """确认不存在的信封：400（客户端重试窗口内信封不会消失）。"""
    header = _auth_header(_login(client))
    resp = client.post(
        "/api/v1/sync/ack",
        json={"envelope_id": "env_not_exists"},
        headers=header,
    )
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "DATA_VALIDATION_FAILED"
    assert error["details"]["reason"] == "SYNC_ENVELOPE_NOT_FOUND"


def test_sync_inject_duplicate_event_id_returns_409(client: TestClient) -> None:
    """同一 event_id 重复注入：409 + DUPLICATE_EVENT（event_id 全局唯一）。"""
    header = _auth_header(_login(client))
    device_id = _register_device(client, header, fingerprint="fp-m3-dup")
    body = {"target_device_id": device_id, "payload": {"x": 1}, "event_id": "evt_dup_001"}

    first = client.post("/api/v1/dev/sync/inject", json=body, headers=header)
    assert first.status_code == 200

    duplicate = client.post("/api/v1/dev/sync/inject", json=body, headers=header)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "DUPLICATE_EVENT"


def test_sync_pull_skips_and_cleans_expired_envelopes(client: TestClient) -> None:
    """TTL 过期信封不下发，且拉取时被顺带清理（spec：断网重试 / TTL 清理）。"""
    header = _auth_header(_login(client))
    device_id = _register_device(client, header, fingerprint="fp-m3-ttl")

    injected = client.post(
        "/api/v1/dev/sync/inject",
        json={"target_device_id": device_id, "payload": {"x": 1}, "ttl_seconds": 60},
        headers=header,
    )
    assert injected.status_code == 200
    envelope_id = injected.json()["data"]["envelope_id"]

    # 直接把投影的过期时间拨到过去（模拟 TTL 已过）
    envelope = _container(client).sync_envelopes.get(envelope_id)
    assert envelope is not None
    envelope.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    pulled = client.get(f"/api/v1/sync/events/pull?device_id={device_id}", headers=header)
    assert pulled.status_code == 200
    assert pulled.json()["data"] == []
    # 过期信封已被清理
    assert _container(client).sync_envelopes.get(envelope_id) is None


def test_sync_isolation_across_tenants(client: TestClient) -> None:
    """跨租户隔离：注入目标必须本商户设备；跨商户 ACK 按不存在拒绝。"""
    header_a = _auth_header(_login(client))
    device_a = _register_device(client, header_a, fingerprint="fp-m3-tenant-a")
    header_b = _add_tenant_with_license(client, "tnt_b", "merchant_b")
    device_b = _register_device(client, header_b, fingerprint="fp-m3-tenant-b")

    # 商户 A 注入到商户 B 的设备：按设备不存在拒绝
    cross = client.post(
        "/api/v1/dev/sync/inject",
        json={"target_device_id": device_b, "payload": {"x": 1}},
        headers=header_a,
    )
    assert cross.status_code == 400
    assert cross.json()["error"]["details"]["reason"] == "DEVICE_NOT_FOUND"

    injected = client.post(
        "/api/v1/dev/sync/inject",
        json={"target_device_id": device_a, "payload": {"x": 1}},
        headers=header_a,
    )
    assert injected.status_code == 200
    envelope_id = injected.json()["data"]["envelope_id"]

    # 商户 B 确认 A 的信封：按不存在拒绝（不泄露存在性）
    ack = client.post(
        "/api/v1/sync/ack",
        json={"envelope_id": envelope_id},
        headers=header_b,
    )
    assert ack.status_code == 400
    assert ack.json()["error"]["details"]["reason"] == "SYNC_ENVELOPE_NOT_FOUND"

    # 商户 B 拉取 A 的设备信封：按设备不存在拒绝
    pulled = client.get(f"/api/v1/sync/events/pull?device_id={device_a}", headers=header_b)
    assert pulled.status_code == 400
    assert pulled.json()["error"]["details"]["reason"] == "DEVICE_NOT_FOUND"


def test_dev_inject_disabled_in_production(client: TestClient) -> None:
    """生产环境禁用 Mock 事件注入端点：403 + AUTH_FORBIDDEN。"""
    header = _auth_header(_login(client))
    device_id = _register_device(client, header, fingerprint="fp-m3-prod")
    _container(client).settings.APP_ENV = "production"

    resp = client.post(
        "/api/v1/dev/sync/inject",
        json={"target_device_id": device_id, "payload": {"x": 1}},
        headers=header,
    )
    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "AUTH_FORBIDDEN"
    assert error["details"]["endpoint"] == "POST /api/v1/dev/sync/inject"


# --------------------------------------------------------------------------
# 状态快照聚合
# --------------------------------------------------------------------------


def test_snapshot_aggregates_pending_sync_count(client: TestClient) -> None:
    """快照待同步数：聚合自各设备最新心跳投影（无心跳投影时为 null）。"""
    header = _auth_header(_login(client))

    empty = client.get("/api/v1/events/snapshot", headers=header)
    assert empty.status_code == 200
    assert empty.json()["data"]["pending_sync_count"] is None

    device_id = _register_device(client, header, fingerprint="fp-m3-snapshot")
    heartbeat = client.post(
        "/api/v1/heartbeat",
        json={"device_id": device_id, "status": "RUNNING", "pending_sync_count": 3},
        headers=header,
    )
    assert heartbeat.status_code == 200

    snapshot = client.get("/api/v1/events/snapshot", headers=header)
    assert snapshot.status_code == 200
    assert snapshot.json()["data"]["pending_sync_count"] == 3
