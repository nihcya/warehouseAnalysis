"""设备注册与列表测试（M2）。

对应 spec：设备注册与许可证约束、Scope 与租户隔离。
覆盖：注册成功、指纹幂等、吊销拒绝、设备数上限、租户隔离、审计留痕、状态流事件。
"""

from __future__ import annotations

from typing import Any

from app.domain.device import DeviceStatus
from conftest import DEMO_MERCHANT_LOGIN, create_merchant, login_token
from fastapi.testclient import TestClient

DEVICE_PAYLOAD = {
    "name": "仓库前台电脑",
    "fingerprint": "fp-office-0001",
    "device_type": "DESKTOP",
    "app_version": "0.2.0",
}


def register(client: TestClient, token: str, **overrides: Any):
    """按默认载荷注册设备（允许覆盖字段）。"""
    payload = {**DEVICE_PAYLOAD, **overrides}
    return client.post(
        "/api/v1/devices/register",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


def test_register_device_success(client: TestClient) -> None:
    """注册成功：200 + 设备信息 + REGISTERED 状态。"""
    token = login_token(client, DEMO_MERCHANT_LOGIN)
    resp = register(client, token)
    assert resp.status_code == 200
    device = resp.json()["data"]
    assert device["device_id"].startswith("dev_")
    assert device["tenant_id"] == "tnt_demo"
    assert device["status"] == "REGISTERED"
    assert device["device_type"] == "DESKTOP"
    assert device["app_version"] == "0.2.0"


def test_register_requires_authentication(client: TestClient) -> None:
    """未登录注册设备：401 AUTH_REQUIRED（不落库）。"""
    resp = client.post("/api/v1/devices/register", json=DEVICE_PAYLOAD)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


def test_developer_scope_cannot_register_device(client: TestClient) -> None:
    """开发者 Scope 注册设备：403 AUTH_FORBIDDEN（设备归属商户）。"""
    from conftest import DEMO_DEVELOPER_LOGIN

    resp = register(client, login_token(client, DEMO_DEVELOPER_LOGIN))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_FORBIDDEN"


def test_register_same_fingerprint_is_idempotent(client: TestClient) -> None:
    """同一指纹重复注册：返回同一台设备并刷新名称，不产生第二条记录。"""
    token = login_token(client, DEMO_MERCHANT_LOGIN)
    first = register(client, token).json()["data"]
    second = register(client, token, name="改名后的电脑", app_version="0.3.0").json()["data"]

    assert first["device_id"] == second["device_id"]
    assert second["name"] == "改名后的电脑"
    assert second["app_version"] == "0.3.0"

    listed = client.get(
        "/api/v1/devices", headers={"Authorization": f"Bearer {token}"}
    ).json()["data"]
    assert len(listed) == 1


def test_register_respects_max_devices(client: TestClient) -> None:
    """超出许可证 max_devices（演示商户 3 台）：403 + DEVICE_LIMIT_EXCEEDED。"""
    token = login_token(client, DEMO_MERCHANT_LOGIN)
    for index in range(3):
        assert register(client, token, fingerprint=f"fp-{index}").status_code == 200

    denied = register(client, token, fingerprint="fp-overflow")
    assert denied.status_code == 403
    error = denied.json()["error"]
    assert error["code"] == "AUTH_FORBIDDEN"
    assert error["details"]["reason"] == "DEVICE_LIMIT_EXCEEDED"
    assert error["details"]["max_devices"] == 3
    assert error["details"]["registered_devices"] == 3


def test_revoked_device_cannot_be_registered_again(
    client: TestClient, container: Any
) -> None:
    """已吊销设备：403 DEVICE_REVOKED（重新绑定前不可恢复，主基线 §32.3）。"""
    token = login_token(client, DEMO_MERCHANT_LOGIN)
    device_id = register(client, token).json()["data"]["device_id"]

    device = container.devices.get_device(device_id)
    assert device is not None
    device.transition(DeviceStatus.REVOKED)
    container.devices.save_device(device)

    resp = register(client, token)
    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "DEVICE_REVOKED"
    assert error["details"]["device_id"] == device_id


def test_device_list_is_scoped_to_own_tenant(client: TestClient, container: Any) -> None:
    """租户隔离：商户只能看到自己的设备。"""
    demo_token = login_token(client, DEMO_MERCHANT_LOGIN)
    register(client, demo_token, fingerprint="fp-demo-1")
    register(client, demo_token, fingerprint="fp-demo-2")

    other_account, _ = create_merchant(container, password="dev-passw0rd")
    other_token = login_token(client, other_account.login_name, "dev-passw0rd")
    register(client, other_token, fingerprint="fp-other-1")

    mine = client.get(
        "/api/v1/devices", headers={"Authorization": f"Bearer {demo_token}"}
    ).json()["data"]
    others = client.get(
        "/api/v1/devices", headers={"Authorization": f"Bearer {other_token}"}
    ).json()["data"]

    assert sorted(device["fingerprint"] for device in mine) == ["fp-demo-1", "fp-demo-2"]
    assert [device["fingerprint"] for device in others] == ["fp-other-1"]
    assert all(device["tenant_id"] == "tnt_demo" for device in mine)


def test_register_writes_audit_and_publishes_event(
    client: TestClient, container: Any
) -> None:
    """注册成功：写审计（SUCCESS）并向状态流发布 device.registered 事件。"""
    from app.domain.audit import AuditAction, AuditResult

    token = login_token(client, DEMO_MERCHANT_LOGIN)
    before = container.hub.latest_event_id

    device_id = register(client, token).json()["data"]["device_id"]

    entries = [
        entry
        for entry in container.audit_repository.list_for_tenant("tnt_demo")
        if entry.action is AuditAction.DEVICE_REGISTER
    ]
    assert entries
    assert entries[-1].result is AuditResult.SUCCESS
    assert entries[-1].target_id == device_id
    assert container.hub.latest_event_id > before


def test_unknown_device_type_falls_back_to_desktop(client: TestClient) -> None:
    """未知 device_type 回退 DESKTOP（工作台是第一客户端），不阻断注册。"""
    token = login_token(client, DEMO_MERCHANT_LOGIN)
    resp = register(client, token, device_type="PDA")
    assert resp.status_code == 200
    assert resp.json()["data"]["device_type"] == "DESKTOP"
