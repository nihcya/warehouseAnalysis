"""session 仓储持久化回归测试（Issue #17）。

``PostgresIdentityRepository.save_session`` 曾漏写 ``expires_at``，
导致 Refresh Token 轮换时**应用层已重算过期时间却没有落库**
（``auth_usecase._rotate`` 会执行 ``session.expires_at = refresh_expiry(now)``），
结果会话 TTL 不随刷新延长，30 天后必须重新登录。

本测试用 **SQLite 内存库** 驱动同一套 ORM 仓储，无需 PostgreSQL 即可覆盖该路径，
用于锁住「save_session 必须持久化全部可变字段」这一契约。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.session import ClientType
from app.domain.session import Session as DomainSession
from app.infrastructure.db.models import Base
from app.infrastructure.db.repositories import PostgresIdentityRepository

#: 统一取整到秒，规避 SQLite 与 PostgreSQL 的微秒精度差异
_NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture()
def repo() -> Iterator[PostgresIdentityRepository]:
    """以 SQLite 内存库驱动 ORM 仓储（不依赖 PostgreSQL service 容器）。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield PostgresIdentityRepository(factory)
    finally:
        engine.dispose()


def _make_session(expires_at: datetime) -> DomainSession:
    return DomainSession(
        session_id="ses_regression",
        account_id="acc_regression",
        client_type=ClientType.WEB,
        refresh_token_hash="hash_old",
        expires_at=expires_at,
        created_at=_NOW,
    )


def _as_utc_naive(value: datetime) -> datetime:
    """SQLite 回读可能丢失 tzinfo，统一折算为 naive UTC 再比较。"""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def test_save_session_persists_expires_at(repo: PostgresIdentityRepository) -> None:
    """轮换后重算的 expires_at 必须落库（Issue #17 回归）。"""
    original = _NOW + timedelta(days=30)
    repo.add_session(_make_session(original))

    stored = repo.get_session("ses_regression")
    assert stored is not None
    assert _as_utc_naive(stored.expires_at) == _as_utc_naive(original)

    # 模拟 auth_usecase._rotate：轮换指纹 + 按新 TTL 重算过期时间
    extended = _NOW + timedelta(days=60)
    stored.rotate("hash_new", _NOW)
    stored.expires_at = extended
    repo.save_session(stored)

    reloaded = repo.get_session("ses_regression")
    assert reloaded is not None
    assert reloaded.refresh_token_hash == "hash_new"
    assert reloaded.previous_refresh_token_hash == "hash_old"
    # 关键断言：未落库时此处会退回原始的 30 天
    assert _as_utc_naive(reloaded.expires_at) == _as_utc_naive(extended)


def test_save_session_persists_rotation_and_revocation(
    repo: PostgresIdentityRepository,
) -> None:
    """轮换痕迹与撤销状态同样必须落库，防止同类遗漏。"""
    repo.add_session(_make_session(_NOW + timedelta(days=30)))

    stored = repo.get_session("ses_regression")
    assert stored is not None
    stored.rotate("hash_rotated", _NOW)
    repo.save_session(stored)

    rotated = repo.get_session("ses_regression")
    assert rotated is not None
    assert rotated.refresh_token_hash == "hash_rotated"
    assert _as_utc_naive(rotated.rotated_at) == _as_utc_naive(_NOW)

    rotated.revoke(_NOW)
    repo.save_session(rotated)

    revoked = repo.get_session("ses_regression")
    assert revoked is not None
    assert revoked.revoked_at is not None
    assert _as_utc_naive(revoked.revoked_at) == _as_utc_naive(_NOW)
    assert not revoked.is_active(_NOW + timedelta(seconds=1))
