"""端到端工作流集成测试（Task 6）。

覆盖：
- 在线完整工作流：启动内存仓储控制平面 → 登录 merchant_demo → 获取账号上下文
  → 注册设备 → 验证设备列表 → 获取状态流快照 → 验证在线状态；
- 离线场景：控制平面不可达时，工作台仍可执行本地导入/分析/报告操作。

控制平面服务目录加入 sys.path 后以 ``import app.*`` 引用（与 control-plane
自身测试同口径），演示账号经 ``CONTROL_PLANE_DEMO_PASSWORD`` 注入内存种子。
端到端测试不需要启动真实 QApplication，用 TestClient 级别验证 API 调用链。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from workbench.application.analysis_usecase import RunAnalysisUseCase
from workbench.application.import_manager import (
    IMPORT_TYPE_MASTER,
    ImportRunSummary,
)
from workbench.application.report_export import ReportExportManager
from workbench.infrastructure.api_client.device_fingerprint import (
    generate_device_fingerprint,
    get_device_name,
)
from workbench.infrastructure.api_client.http_client import HttpApiClient
from workbench.infrastructure.api_client.token_store import TokenStore
from workbench.main import create_report_export_manager

#: 仓库根（apps/workbench-desktop/tests → apps/workbench-desktop → apps → 仓库根）
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: 控制平面服务目录（加入 sys.path 使 import app.* 可用）
_SERVICE_ROOT = str(_REPO_ROOT / "services" / "control-plane")
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

#: 演示账号登录名
DEMO_MERCHANT_LOGIN = "merchant_demo"

#: 演示密码环境变量名
DEMO_PASSWORD_ENV = "CONTROL_PLANE_DEMO_PASSWORD"

#: 演示账号固定密码（经 DEMO_PASSWORD_ENV 注入内存种子）
DEMO_PASSWORD = "unit-test-demo-pass"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def control_plane_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """内存仓储 + 固定演示密码的控制平面 FastAPI 应用。"""
    # 在导入控制平面模块前设置环境变量，确保 get_settings 读到内存仓储
    monkeypatch.setenv(DEMO_PASSWORD_ENV, DEMO_PASSWORD)
    monkeypatch.setenv("CONTROL_PLANE_REPOSITORY", "memory")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_SECRET", "unit-test-secret")
    # 不可达端口让 /health 的数据库探测快速失败（不等待超时）
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/x")

    from app.container import build_container
    from app.main import create_app
    from app.settings import REPOSITORY_MEMORY, Settings, get_settings

    # 清除 lru_cache，确保读到上面设置的环境变量
    get_settings.cache_clear()

    settings = Settings(
        APP_ENV="dev",
        AUTH_SECRET="unit-test-secret",
        CONTROL_PLANE_REPOSITORY=REPOSITORY_MEMORY,
    )
    container = build_container(settings)
    return create_app(container=container)


@pytest.fixture()
def online_api_client(
    control_plane_app: Any,
    tmp_path: Path,
) -> HttpApiClient:
    """指向内存控制平面的 HttpApiClient（注入 Starlette TestClient 做 ASGI 桥接）。"""
    from fastapi.testclient import TestClient as FastAPITestClient

    store = TokenStore(data_dir=tmp_path / "auth")
    test_client = FastAPITestClient(control_plane_app)
    return HttpApiClient(
        base_url="http://testserver",
        token_store=store,
        client=test_client,
    )


@pytest.fixture()
def offline_client(tmp_path: Path) -> HttpApiClient:
    """指向不可达地址的 HttpApiClient（离线场景）。

    用 MockTransport 模拟连接被拒，避免依赖真实端口状态。
    """
    store = TokenStore(data_dir=tmp_path / "auth-offline")

    def _refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("连接被拒绝", request=request)

    mock_client = httpx.Client(
        base_url="http://unreachable.test",
        transport=httpx.MockTransport(_refuse),
    )
    return HttpApiClient(
        base_url="http://unreachable.test",
        token_store=store,
        client=mock_client,
    )


# --------------------------------------------------------------------------
# 在线完整工作流
# --------------------------------------------------------------------------


def test_full_workflow_online(online_api_client: HttpApiClient) -> None:
    """在线完整工作流：登录 → 账号上下文 → 注册设备 → 设备列表 → 状态快照 → 在线。

    用内存仓储控制平面（FastAPI TestClient）作为后端，验证 HttpApiClient
    的完整 API 调用链：login → get_account_me → register_device → list_devices
    → get_snapshot，全程 online=True。
    """
    client = online_api_client

    # 1. 登录 merchant_demo
    auth_data = client.login(DEMO_MERCHANT_LOGIN, DEMO_PASSWORD)
    assert auth_data is not None, "登录应成功"
    assert auth_data["account"]["login_name"] == DEMO_MERCHANT_LOGIN
    assert auth_data["license"]["status"] == "ACTIVE"
    assert auth_data["tokens"]["access_token"]

    # 2. 获取账号上下文，验证令牌有效
    me = client.get_account_me()
    assert me is not None, "令牌应有效"
    assert me["account"]["login_name"] == DEMO_MERCHANT_LOGIN
    assert me["tenant"]["tenant_id"]
    assert me["license"]["status"]

    # 3. 注册设备
    fingerprint = generate_device_fingerprint()
    name = get_device_name()
    registered = client.register_device(name, fingerprint)
    assert registered is not None, "设备注册应成功"
    device_id = registered["device_id"]
    assert device_id
    assert registered["fingerprint"] == fingerprint

    # 4. 验证设备列表包含已注册设备
    devices = client.list_devices()
    assert devices is not None
    assert any(d["device_id"] == device_id for d in devices)

    # 5. 获取状态流快照，验证许可证与设备列表可用
    snapshot = client.get_snapshot()
    assert snapshot is not None
    assert snapshot["event_id"] is not None
    assert snapshot["license"]["status"]
    assert isinstance(snapshot["devices"], list)

    # 6. 验证在线状态
    assert client.online is True


# --------------------------------------------------------------------------
# 离线场景：本地业务不受阻
# --------------------------------------------------------------------------


def test_offline_workflow_local_unchanged(
    offline_client: HttpApiClient,
    fake_provider,
    store,
    golden_input,
    import_manager,
    session_factory,
    data_dir,
    tmp_path,
) -> None:
    """离线场景：控制平面不可达时本地业务（导入/分析/报告）仍可正常使用。

    验证离线模式下不依赖 HTTP 的本地功能链路：
    - HttpApiClient.online == False，health 返回 CLIENT_OFFLINE 占位；
    - 本地分析（FakeEngine + golden fixture）正常执行并产出结果；
    - 本地 CSV 导入正常执行并完成；
    - 报告导出管理器可正常创建。
    """
    # 1. 验证离线状态
    assert offline_client.online is False
    health = offline_client.health()
    assert health["error"]["code"] == "CLIENT_OFFLINE"

    # 2. 验证本地分析仍可运行（不需要 HTTP）
    use_case = RunAnalysisUseCase(fake_provider, store, golden_input)
    outcome = use_case.run()
    assert outcome.ok, "本地分析应成功"
    assert outcome.result is not None

    # 3. 验证本地 CSV 导入仍可运行（不需要 HTTP）
    master_csv = tmp_path / "e2e-offline-master.csv"
    master_csv.write_text(
        "sku_id,name,category,unit,unit_cost\n"
        "SKU-E2E-01,测试商品A,测试,个,1.00\n"
        "SKU-E2E-02,测试商品B,测试,个,2.00\n",
        encoding="utf-8",
    )
    mapping = {
        "sku_id": "sku_id",
        "name": "name",
        "category": "category",
        "unit": "unit",
        "unit_cost": "unit_cost",
    }
    summary = import_manager.run_import(
        path=master_csv,
        import_type=IMPORT_TYPE_MASTER,
        mapping=mapping,
    )
    assert isinstance(summary, ImportRunSummary)
    assert summary.completed, "本地导入应成功"

    # 4. 验证报告导出管理器仍可创建（不需要 HTTP）
    report_manager = create_report_export_manager(session_factory, data_dir)
    assert isinstance(report_manager, ReportExportManager)
