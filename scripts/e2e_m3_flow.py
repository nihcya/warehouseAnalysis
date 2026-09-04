"""e2e_m3_flow.py：M3 端到端联调脚本（Task 7.2）。

全程 tmp 目录（不触碰真实数据目录），内存仓储控制平面 + ASGI 桥接，完整链路：
登录 → 设备注册 → 心跳 → 配置验签 → 任务拉取执行 → Mock 事件注入
→ 拉取落库 → ACK → 断网重试（ACK 断链 / 全断网）→ 幂等重放。

运行（仓库根）::

    uv run python scripts/e2e_m3_flow.py

退出码：0 = 全部通过；1 = 存在失败项。

说明：
- 控制平面以内存仓储（``CONTROL_PLANE_REPOSITORY=memory``）注入容器，
  事件加密密钥与配置签名密钥显式传入 Settings，保证云端签发/加密与
  工作台验签/解密口径确定一致；
- workbench-desktop 与 control-plane 的顶层包名同为 ``app``，故按
  apps/workbench-desktop/tests/conftest.py 的方式把 workbench 的 app
  目录注册为顶层包 ``workbench``（不能直接注入 sys.path）；
- 断网用 ``httpx.MockTransport`` 模拟：选择性断 ACK（验证 PENDING 重发）
  与全断网（验证 pull 失败退避）两种路径。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTROL_PLANE_ROOT = REPO_ROOT / "services" / "control-plane"
LOCAL_DATA_SRC = REPO_ROOT / "local-data" / "src"
WORKBENCH_APP_DIR = REPO_ROOT / "apps" / "workbench-desktop" / "app"

for _path in (str(CONTROL_PLANE_ROOT), str(LOCAL_DATA_SRC)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# workbench 别名注册（照抄 apps/workbench-desktop/tests/conftest.py 的权威实现）
WORKBENCH_ALIAS = "workbench"
if WORKBENCH_ALIAS not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        WORKBENCH_ALIAS,
        WORKBENCH_APP_DIR / "__init__.py",
        submodule_search_locations=[str(WORKBENCH_APP_DIR)],
    )
    assert _spec is not None and _spec.loader is not None
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[WORKBENCH_ALIAS] = _module
    _spec.loader.exec_module(_module)

import httpx
from alembic import command
from alembic.config import Config
from app.container import build_container
from app.infrastructure.memory.seed import (
    DEMO_CONFIG_VERSION,
    DEMO_MERCHANT_LOGIN,
    DEMO_PASSWORD_ENV,
)
from app.main import create_app
from app.settings import REPOSITORY_MEMORY, Settings, get_settings
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from local_data.connection import connect, database_url
from local_data.repository import (
    InventoryEventRepository,
    MasterDataRepository,
)
from PySide6.QtWidgets import QApplication
from workbench.agent.agent_worker import AgentWorker, verify_config_payload
from workbench.infrastructure.api_client.device_fingerprint import (
    generate_device_fingerprint,
)
from workbench.infrastructure.api_client.http_client import HttpApiClient
from workbench.infrastructure.api_client.token_store import TokenStore
from workbench.workers.sync_worker import SyncWorker

DEMO_PASSWORD = "e2e-demo-pass"
SIGNING_SECRET = "e2e-signing-secret"
OCCURRED_AT = "2026-09-01T08:00:00+00:00"

PASSED: list[str] = []
FAILED: list[str] = []


def check(ok: bool, message: str) -> None:
    """单步断言：通过计入 PASSED，失败计入 FAILED（不中断后续步骤）。"""
    if ok:
        PASSED.append(message)
        print(f"[ok] {message}")
    else:
        FAILED.append(message)
        print(f"[FAIL] {message}")


def make_payload(event_id: str, quantity: str = "100") -> dict:
    """构造小程序库存事件明文（SyncWorker._build_event 必填 7 字段）。"""
    return {
        "event_id": event_id,
        "sku_id": "SKU-0001",
        "warehouse_id": "WH-01",
        "move_type": "INBOUND",
        "quantity": quantity,
        "occurred_at": OCCURRED_AT,
        "source": "MINIAPP",
    }


def inject_event(
    test_client: TestClient,
    headers: dict[str, str],
    device_id: str,
    event_id: str,
) -> httpx.Response:
    """POST /dev/sync/inject：Mock 小程序事件 → 云端加密信封（merchant 令牌）。"""
    return test_client.post(
        "/api/v1/dev/sync/inject",
        json={
            "target_device_id": device_id,
            "payload": make_payload(event_id),
            "event_id": event_id,
        },
        headers=headers,
    )


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # 演示密码必须在 build_container 播种之前固定（resolve_demo_password 读环境变量）
    os.environ[DEMO_PASSWORD_ENV] = DEMO_PASSWORD
    if QApplication.instance() is None:
        QApplication([])

    with tempfile.TemporaryDirectory(prefix="e2e-m3-") as tmp_name:
        tmp = Path(tmp_name)
        data_dir = tmp / "data"

        print("\n[step 0] 组合根：内存控制平面 + ASGI 桥接 + 本地库迁移")
        fernet_key = Fernet.generate_key().decode()
        get_settings.cache_clear()
        settings = Settings(
            APP_ENV="dev",
            AUTH_SECRET="e2e-secret",
            CONTROL_PLANE_REPOSITORY=REPOSITORY_MEMORY,
            SYNC_ENCRYPTION_KEY=fernet_key,
            CONFIG_SIGNING_SECRET=SIGNING_SECRET,
        )
        container = build_container(settings)
        test_client = TestClient(create_app(container=container))
        client = HttpApiClient(
            base_url="http://testserver",
            token_store=TokenStore(tmp / "auth"),
            client=test_client,
        )
        cfg = Config()
        cfg.set_main_option("script_location", str(REPO_ROOT / "local-data" / "alembic"))
        cfg.set_main_option("sqlalchemy.url", database_url(data_dir))
        command.upgrade(cfg, "head")
        engine, session_factory = connect(data_dir)
        check(True, "内存控制平面与本地库（alembic head）就绪")

        try:
            master = MasterDataRepository(session_factory)
            master.add_warehouse(warehouse_id="WH-01", name="主仓")
            master.add_sku(sku_id="SKU-0001", name="矿泉水 550ml", category="饮料", unit="瓶")

            print("\n[step 1] 登录与设备注册")
            auth = client.login(DEMO_MERCHANT_LOGIN, DEMO_PASSWORD)
            check(auth is not None, "merchant_demo 登录成功")
            reg = client.register_device("e2e-agent", generate_device_fingerprint())
            device_id = str((reg or {}).get("device_id", ""))
            check(bool(device_id), "设备注册成功（device_id 就绪）")
            headers = {"Authorization": f"Bearer {client.access_token}"}

            print("\n[step 2] Agent：心跳 → 配置验签 → 任务拉取执行")
            agent = AgentWorker(
                client, device_id, signing_secret=SIGNING_SECRET, config_cache_dir=tmp / "agent"
            )
            check(agent._beat() is True, "心跳上报成功")
            agent._refresh_config()
            cached = agent.load_cached_config()
            check(cached is not None, "配置拉取并写入本地缓存")
            check(verify_config_payload(cached or {}, SIGNING_SECRET), "配置摘要与 HMAC 签名验签通过")
            check(
                cached is not None and cached.get("version") == DEMO_CONFIG_VERSION,
                "配置版本与云端演示版本一致",
            )
            executed: list[tuple[str, bool]] = []
            agent.task_executed.connect(
                lambda task_id, ok: executed.append((str(task_id), bool(ok)))
            )
            agent._pull_and_execute_tasks()
            check(
                len(executed) == 1 and executed[0][1],
                "任务拉取执行：种子唯一 CREATED 运行被消耗",
            )
            check(client.pull_tasks(device_id) == [], "重复任务拉取为空（QUEUED 锁定不重复分发）")

            print("\n[step 3] 同步：空轮 → 注入 2 事件 → 拉取落库 → ACK")
            worker = SyncWorker(
                api_client=client,
                session_factory=session_factory,
                device_id=device_id,
                key=fernet_key,
            )
            check(worker._run_cycle() == (0, 0, 0, True), "同步空轮：无信封、无待重试 ACK")
            envelope_ids: list[str] = []
            for seq in (1, 2):
                event_id = f"EVT-E2E-{seq:04d}"
                resp = inject_event(test_client, headers, device_id, event_id)
                data = resp.json().get("data", {}) if resp.status_code == 200 else {}
                envelope_ids.append(str(data.get("envelope_id", "")))
                check(
                    bool(resp.status_code == 200 and envelope_ids[-1]),
                    f"云端注入事件 {event_id}（加密信封就绪）",
                )
            check(worker._run_cycle() == (2, 0, 0, True), "拉取解密落库 2 事件并 ACK 成功")

            events = {
                row.event_id: row
                for row in InventoryEventRepository(session_factory).list_events()
            }
            check(set(events) == {"EVT-E2E-0001", "EVT-E2E-0002"}, "本地事件落库恰好 2 条")
            evt = events.get("EVT-E2E-0001")
            check(
                evt is not None
                and evt.sku_id == "SKU-0001"
                and evt.warehouse_id == "WH-01"
                and evt.move_type == "INBOUND"
                and evt.quantity == Decimal(100)
                and evt.occurred_at == OCCURRED_AT
                and evt.source == "MINIAPP",
                "落库字段逐项匹配（SKU/仓库/方向/数量/时间/来源）",
            )

            print("\n[step 4] 幂等重放")
            check(worker._run_cycle() == (0, 0, 0, True), "重复同步：云端不下发已 ACK 信封")
            reack = client.ack_sync_events(envelope_ids[0])
            check(
                reack is not None and reack.get("already_acked") is True,
                "重复 ACK 幂等（already_acked=True）",
            )
            dup = inject_event(test_client, headers, device_id, "EVT-E2E-0001")
            check(dup.status_code == 409, "云端拒绝重复 event_id 注入（409）")

            print("\n[step 5] 断网重试一：仅 ACK 不可达（落库成功为准，PENDING 重发）")
            inject_event(test_client, headers, device_id, "EVT-E2E-0004")
            original = client._client

            def block_ack(request: httpx.Request) -> httpx.Response:
                if request.method == "POST" and request.url.path == "/api/v1/sync/ack":
                    raise httpx.ConnectError("ack blocked", request=request)
                return test_client.request(
                    request.method,
                    request.url.path,
                    params=str(request.url.params) or None,
                    headers=dict(request.headers),
                    content=request.read(),
                )

            blocked = httpx.Client(
                base_url="http://testserver", transport=httpx.MockTransport(block_ack)
            )
            client._client = blocked
            try:
                check(worker._run_cycle() == (1, 0, 1, True), "ACK 失败：落库成功且 pending=1（不回滚）")
            finally:
                client._client = original
                blocked.close()
            check(worker._run_cycle() == (0, 0, 0, True), "恢复后重发 PENDING ACK 成功（下轮清零）")

            print("\n[step 6] 断网重试二：全断网（pull 失败 → ok=False 退避）")
            inject_event(test_client, headers, device_id, "EVT-E2E-0003")

            def refuse(request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("connection refused", request=request)

            refused = httpx.Client(
                base_url="http://testserver", transport=httpx.MockTransport(refuse)
            )
            client._client = refused
            try:
                check(worker._run_cycle() == (0, 0, 0, False), "断网时 _run_cycle 返回 ok=False")
            finally:
                client._client = original
                refused.close()
            check(worker._run_cycle() == (1, 0, 0, True), "恢复后重试成功（事件落库并 ACK）")
            check(
                len(InventoryEventRepository(session_factory).list_events()) == 4,
                "最终本地共 4 条事件（无重复、无丢失）",
            )
        finally:
            engine.dispose()

    print()
    print("=" * 60)
    print(f"PASSED: {len(PASSED)}  FAILED: {len(FAILED)}")
    if FAILED:
        print("失败项：")
        for item in FAILED:
            print(f"  - {item}")
    print("=" * 60)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
