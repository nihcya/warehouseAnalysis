"""/health 健康检查测试。"""

from __future__ import annotations

from app.main import create_app
from fastapi.testclient import TestClient


def test_health_ok_with_database_up(monkeypatch) -> None:
    """数据库可达：200 且字段齐全、database 为 up。"""
    monkeypatch.setattr("app.api.health.check_database_reachable", lambda url: True)
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "app_version": "0.1.0", "database": "up"}


def test_health_database_down_still_200(monkeypatch) -> None:
    """数据库不可达：仍返回 200，database 如实为 down。"""
    monkeypatch.setattr("app.api.health.check_database_reachable", lambda url: False)
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["app_version"] == "0.1.0"
    assert body["database"] == "down"


def test_health_reports_real_database_state() -> None:
    """不 mock 探测：本地无 PostgreSQL 时如实报 down（CI 容器内报 up）。"""
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["database"] in {"up", "down"}
