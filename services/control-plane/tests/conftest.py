"""control-plane 测试配置。

服务不以可分发包安装（tool.uv package=false），
此处把服务目录加入 sys.path，使 ``import app.*`` 在仓库根运行 pytest 时可用。

M2：测试默认注入**内存仓储**容器（``CONTROL_PLANE_REPOSITORY=memory``），
这样在没有 PostgreSQL 的机器上也能覆盖认证、授权、许可证与状态流逻辑；
PostgreSQL 实现的正确性由 CI 的 service 容器迁移测试保证。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = Path(__file__).resolve().parent

if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))
# 允许测试文件 ``from conftest import ...`` 复用本文件的辅助函数
# （pyproject 使用 --import-mode=importlib，pytest 不会自动插入测试目录）
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from app.container import Container, build_container
from app.domain.account import Account, AccountRole
from app.infrastructure.ids import PREFIX_ACCOUNT, new_id
from app.infrastructure.memory.seed import (
    DEMO_DEVELOPER_LOGIN,
    DEMO_MERCHANT_LOGIN,
    DEMO_PASSWORD_ENV,
    new_tenant,
)
from app.main import create_app
from app.settings import Settings

#: 测试用固定口令（写入环境变量供演示种子读取，不入代码常量）
TEST_PASSWORD = "test-passw0rd"

#: 测试用签名密钥（HS256 要求 >= 32 字节，避免 PyJWT 的密钥长度告警）
TEST_AUTH_SECRET = "test-auth-secret-0123456789abcdefghij"


def build_test_container(*, password: str = TEST_PASSWORD) -> Container:
    """构建注入内存仓储的测试容器（演示种子口令固定，便于断言）。"""
    os.environ[DEMO_PASSWORD_ENV] = password
    settings = Settings(
        APP_ENV="test",
        CONTROL_PLANE_REPOSITORY="memory",
        AUTH_SECRET=TEST_AUTH_SECRET,
    )
    return build_container(settings)


@pytest.fixture()
def container() -> Container:
    """每个用例一个全新容器（数据隔离，互不影响）。"""
    return build_test_container()


@pytest.fixture()
def client(container: Container) -> Iterator[TestClient]:
    """带内存仓储的 API 测试客户端。"""
    with TestClient(create_app(container)) as test_client:
        yield test_client


def login(client: TestClient, username: str, password: str = TEST_PASSWORD) -> dict:
    """调用登录接口并返回响应体；测试自行断言状态码。"""
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    return response.json()


def auth_headers(client: TestClient, username: str = DEMO_MERCHANT_LOGIN) -> dict[str, str]:
    """登录并返回 Authorization 头（默认商户账号）。"""
    body = login(client, username)
    return {"Authorization": f"Bearer {body['data']['tokens']['access_token']}"}


def developer_headers(client: TestClient) -> dict[str, str]:
    """登录开发者账号并返回 Authorization 头。"""
    return auth_headers(client, DEMO_DEVELOPER_LOGIN)


def create_merchant(
    container: Container,
    *,
    password: str,
    name: str = "另一个商户",
    with_license: bool = True,
    max_devices: int = 3,
    license_days: int = 365,
) -> tuple[Account, str]:
    """追加一个商户（租户 + 可选许可证 + 主账号），租户隔离测试用。"""
    from datetime import UTC, datetime, timedelta

    from app.domain.license import License, LicenseStatus
    from app.infrastructure.auth.passwords import hash_password
    from app.infrastructure.ids import PREFIX_LICENSE
    from app.infrastructure.memory.seed import DEMO_PRODUCT_PROFILE_ID

    tenant_id = new_tenant(container.identity, name=name)
    if with_license:
        today = datetime.now(UTC).date()
        container.entitlements.add_license(
            License(
                license_id=new_id(PREFIX_LICENSE),
                tenant_id=tenant_id,
                product_profile_id=DEMO_PRODUCT_PROFILE_ID,
                starts_at=today,
                expires_at=today + timedelta(days=license_days),
                max_devices=max_devices,
                status=LicenseStatus.ACTIVE,
            )
        )
    account = Account(
        account_id=new_id(PREFIX_ACCOUNT),
        login_name=f"merchant_{new_id('usr')}",
        password_hash=hash_password(password),
        role=AccountRole.MERCHANT_OWNER,
        tenant_id=tenant_id,
    )
    container.identity.add_account(account)
    return account, tenant_id


def login_token(
    client: TestClient,
    username: str,
    password: str = TEST_PASSWORD,
) -> str:
    """登录并返回 access_token（测试断言失败时直接 KeyError，便于定位）。"""
    body = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    ).json()
    return str(body["data"]["tokens"]["access_token"])
