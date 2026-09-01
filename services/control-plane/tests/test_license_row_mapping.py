"""license 行 → 领域对象映射回归测试（Issue #18）。

背景：迁移 ``0003_license_product_feature`` 中 ``license.starts_at`` /
``expires_at`` 为 ``sa.Date()``，而 ORM 曾误注解为 ``Mapped[datetime]``，
读取时调用 ``.date()`` 抛出 ``AttributeError``（``datetime.date`` 无该方法），
使生产 PostgreSQL 路径下查询许可证直接崩溃。

本文件在**无 PostgreSQL**环境下即可覆盖该路径：直接构造 ``LicenseRow``
并调用 ``_license_from_row``，不依赖 session 与数据库。
"""

from __future__ import annotations

import typing
from datetime import UTC, date, datetime

from sqlalchemy import Date

from app.domain.license import License, LicenseStatus
from app.infrastructure.db.models import LicenseRow
from app.infrastructure.db.repositories import _license_from_row


def _make_row() -> LicenseRow:
    """构造一条内存中的 license 行（不落库）。"""
    return LicenseRow(
        license_id="lic_regression",
        tenant_id="tnt_regression",
        product_profile_id="ppf_regression",
        starts_at=date(2026, 1, 1),
        expires_at=date(2027, 1, 1),
        max_devices=3,
        status=LicenseStatus.ACTIVE.value,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_license_from_row_returns_date_not_datetime() -> None:
    """读取路径不得调用 .date()：date 列应原样映射到领域模型的 date 字段。"""
    license_ = _license_from_row(_make_row())

    assert isinstance(license_, License)
    assert license_.starts_at == date(2026, 1, 1)
    assert license_.expires_at == date(2027, 1, 1)
    # datetime 是 date 的子类，需显式排除，防止再次退回 datetime 语义
    assert not isinstance(license_.starts_at, datetime)
    assert not isinstance(license_.expires_at, datetime)


def test_license_columns_are_date_type() -> None:
    """ORM 列类型必须与迁移中的 sa.Date 保持一致。"""
    assert isinstance(LicenseRow.__table__.c.starts_at.type, Date)
    assert isinstance(LicenseRow.__table__.c.expires_at.type, Date)


def _annotation_target(hint: object) -> object:
    """剥离 ``Mapped[...]`` 包装，取出注解指向的实际类型。"""
    args = typing.get_args(hint)
    return args[0] if args else hint


def test_license_annotations_use_date_not_datetime() -> None:
    """注解须为 date：这是 #18 的直接根因，防止后续改动再次漂移。"""
    hints = typing.get_type_hints(LicenseRow, include_extras=True)

    assert _annotation_target(hints["starts_at"]) is date
    assert _annotation_target(hints["expires_at"]) is date


def test_license_from_row_matches_domain_period_semantics() -> None:
    """许可证口径按自然日比较（license_usecase 以 UTC 自然日判断到期）。"""
    license_ = _license_from_row(_make_row())

    assert (license_.expires_at - license_.starts_at).days == 365
    assert license_.max_devices == 3
    assert license_.status is LicenseStatus.ACTIVE
