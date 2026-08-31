"""审计服务测试（M2）。

主基线 §30.3 / §35.10：所有写操作生成 request_id 与审计记录；
日志中不保留密码、令牌与被删除业务数据原文。

覆盖：登录成功/失败留痕、设备注册留痕、注销留痕、字段白名单过滤、
审计只追加（仓储无更新与删除入口）。
"""

from __future__ import annotations

from typing import Any

from app.application.audit import AuditService
from app.domain.audit import (
    ALLOWED_DETAIL_KEYS,
    AuditAction,
    AuditEntry,
    AuditResult,
)
from conftest import DEMO_MERCHANT_LOGIN, TEST_PASSWORD, login_token
from fastapi.testclient import TestClient


def test_login_success_is_audited(client: TestClient, container: Any) -> None:
    """登录成功：写 SUCCESS 审计，含账号、租户与 request_id。"""
    token = login_token(client, DEMO_MERCHANT_LOGIN)
    assert token

    entries = [
        entry
        for entry in container.audit_repository.list_for_tenant("tnt_demo")
        if entry.action is AuditAction.AUTH_LOGIN
    ]
    assert len(entries) == 1
    entry = entries[0]
    assert entry.result is AuditResult.SUCCESS
    assert entry.tenant_id == "tnt_demo"
    assert entry.request_id
    assert entry.actor_role == "MERCHANT_OWNER"


def test_login_failure_is_audited_without_secret(client: TestClient, container: Any) -> None:
    """登录失败：写 DENIED 审计，且审计内容不含密码原文。"""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": DEMO_MERCHANT_LOGIN, "password": "wrong-password"},
    )
    assert resp.status_code == 401

    entries = [
        entry
        for entry in container.audit_repository.list_for_tenant("tnt_demo")
        if entry.action is AuditAction.AUTH_LOGIN
    ]
    assert len(entries) == 1
    entry = entries[0]
    assert entry.result is AuditResult.DENIED
    assert entry.detail["reason"] == "BAD_CREDENTIALS"
    assert "wrong-password" not in str(entry)
    assert TEST_PASSWORD not in str(entry)


def test_unknown_account_login_audit_has_no_actor(client: TestClient, container: Any) -> None:
    """账号不存在：审计仍留痕（actor 为空，保留登录名便于排查撞库）。"""
    client.post(
        "/api/v1/auth/login", json={"username": "ghost", "password": "whatever"}
    )
    entries = container.audit_repository.list_for_actor(None)
    assert any(
        entry.action is AuditAction.AUTH_LOGIN
        and entry.result is AuditResult.DENIED
        and entry.detail.get("reason") == "ACCOUNT_NOT_FOUND"
        for entry in entries
    )


def test_device_register_is_audited(client: TestClient, container: Any) -> None:
    """设备注册：写 SUCCESS 审计并指向设备。"""
    token = login_token(client, DEMO_MERCHANT_LOGIN)
    resp = client.post(
        "/api/v1/devices/register",
        json={"name": "审计测试电脑", "fingerprint": "fp-audit"},
        headers={"Authorization": f"Bearer {token}"},
    )
    device_id = resp.json()["data"]["device_id"]

    entries = [
        entry
        for entry in container.audit_repository.list_for_tenant("tnt_demo")
        if entry.action is AuditAction.DEVICE_REGISTER
    ]
    assert entries
    assert entries[-1].result is AuditResult.SUCCESS
    assert entries[-1].target_type == "device"
    assert entries[-1].target_id == device_id


def test_logout_is_audited(client: TestClient, container: Any) -> None:
    """注销：写 SUCCESS 审计并指向会话。"""
    token = login_token(client, DEMO_MERCHANT_LOGIN)
    client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})

    entries = [
        entry
        for entry in container.audit_repository.list_for_tenant("tnt_demo")
        if entry.action is AuditAction.AUTH_LOGOUT
    ]
    assert len(entries) == 1
    assert entries[0].result is AuditResult.SUCCESS
    assert entries[0].target_type == "session"


def test_refresh_replay_is_audited(client: TestClient, container: Any) -> None:
    """刷新重放：写 DENIED 审计并记录原因码。"""
    tokens = client.post(
        "/api/v1/auth/login",
        json={"username": DEMO_MERCHANT_LOGIN, "password": TEST_PASSWORD},
    ).json()["data"]["tokens"]
    client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    entries = [
        entry
        for entry in container.audit_repository.list_for_tenant("tnt_demo")
        if entry.action is AuditAction.AUTH_REFRESH and entry.result is AuditResult.DENIED
    ]
    assert entries
    assert entries[-1].detail["reason"] == "REFRESH_TOKEN_REUSED"


def test_audit_detail_is_filtered_by_whitelist(container: Any) -> None:
    """detail 白名单：越界键与嵌套结构被丢弃，审计表不会成为敏感数据旁路。"""
    service = AuditService(container.audit_repository)
    entry = service.record(
        action=AuditAction.AUTH_LOGIN,
        result=AuditResult.DENIED,
        tenant_id="tnt_demo",
        detail={
            "reason": "BAD_CREDENTIALS",  # 白名单内
            "password": "p@ssw0rd",  # 越界：丢弃
            "refresh_token": "abc",  # 越界：丢弃
            "nested": {"sku": "SKU-1", "qty": 10},  # 非标量：丢弃
        },
    )
    assert entry.detail == {"reason": "BAD_CREDENTIALS"}
    assert "password" not in str(entry.detail)
    assert "reason" in ALLOWED_DETAIL_KEYS


def test_audit_entries_are_append_only(container: Any) -> None:
    """审计仓储只追加：不提供更新与删除入口（防止事后篡改）。"""
    repository = container.audit_repository
    assert not hasattr(repository, "update")
    assert not hasattr(repository, "delete")

    service = AuditService(repository)
    first: AuditEntry = service.record(
        action=AuditAction.AUTH_LOGIN, result=AuditResult.SUCCESS, tenant_id="tnt_demo"
    )
    service.record(
        action=AuditAction.AUTH_LOGIN, result=AuditResult.DENIED, tenant_id="tnt_demo"
    )
    entries = repository.list_for_tenant("tnt_demo")
    assert len(entries) == 2
    # 先写的不被后写的覆盖
    assert entries[0].audit_id == first.audit_id
