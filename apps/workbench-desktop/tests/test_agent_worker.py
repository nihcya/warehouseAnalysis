"""AgentWorker 测试：心跳退避、配置验签、任务执行（fake client，不真实网络）。

使用 pytest-qt（offscreen 模式）与 monkeypatch 隔离环境变量；
fake http client 按脚本逐次返回，覆盖：

1. 心跳成功：heartbeat_sent(True)，配置与任务按轮次推进；
2. 心跳失败：heartbeat_sent(False) + error_occurred，恢复后立即重报（无退避等待）；
3. 配置验签通过：真实 SHA-256/HMAC 签名 → config_applied + agent_config.json 落盘；
4. 配置验签失败：篡改签名 → config_rejected，保留旧缓存；
5. 任务执行：pull_tasks 返回任务 → task_executed 信号（占位执行成功）；
6. stop() 能在等待期内及时停止线程。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from workbench.agent.agent_worker import (
    AGENT_CONFIG_FILENAME,
    BACKOFF_BASE_SECONDS,
    AgentWorker,
    config_signature,
    verify_config_payload,
)

# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------

SECRET = "test-signing-secret"
CONTENT: dict[str, Any] = {"heartbeat_interval": 30, "log_level": "INFO"}


def _signed_config(version: str = "v1") -> dict[str, Any]:
    """构造带真实摘要与 HMAC 签名的 GET /config 载荷（与云端同口径）。"""
    canonical = json.dumps(
        CONTENT, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    return {
        "tenant_id": "TNT-001",
        "version": version,
        "content": CONTENT,
        "sha256": digest,
        "signature": config_signature(SECRET, version, digest),
        "schema_version": 1,
        "status": "PUBLISHED",
    }


class FakeApiClient:
    """测试用客户端桩：心跳/配置/任务按脚本逐次返回。"""

    def __init__(
        self,
        heartbeat_results: list[dict[str, Any] | None] | None = None,
        config: dict[str, Any] | None = None,
        tasks: list[dict[str, Any]] | None = None,
    ) -> None:
        self._heartbeat_results = list(heartbeat_results or [])
        self._config = config
        self._tasks = tasks or []
        self.heartbeat_calls: list[dict[str, Any]] = []
        self.pull_calls: list[tuple[str, int]] = []

    def send_heartbeat(self, device_id: str, status: str, **kwargs: Any) -> dict[str, Any] | None:
        self.heartbeat_calls.append({"device_id": device_id, "status": status, **kwargs})
        if self._heartbeat_results:
            return self._heartbeat_results.pop(0)
        return {"device_id": device_id}

    def get_config(self) -> dict[str, Any] | None:
        return self._config

    def pull_tasks(self, device_id: str, limit: int = 10) -> list[dict[str, Any]] | None:
        self.pull_calls.append((device_id, limit))
        return self._tasks


def _make_worker(
    client: FakeApiClient,
    cache_dir: Path,
    interval: float = 1.0,
) -> AgentWorker:
    """工厂：tmp 缓存目录 + 注入签名密钥 + 短心跳间隔。"""
    return AgentWorker(
        client,
        device_id="DEV-001",
        heartbeat_interval=interval,
        signing_secret=SECRET,
        config_cache_dir=cache_dir,
    )


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离签名密钥环境变量（Worker 用注入密钥，不受环境影响）。"""
    monkeypatch.delenv("CONFIG_SIGNING_SECRET", raising=False)


# --------------------------------------------------------------------------
# 1. 心跳成功 / 载荷
# --------------------------------------------------------------------------


def test_heartbeat_success_and_payload(qtbot, tmp_path, clean_env):
    """心跳成功：heartbeat_sent(True)，载荷含版本三元组与待同步数。"""
    client = FakeApiClient(heartbeat_results=[{"device_id": "DEV-001"}])
    worker = _make_worker(client, tmp_path / "cache")
    worker._interval = 3600.0
    worker._pending_sync_provider = lambda: 7

    received: list[bool] = []
    worker.heartbeat_sent.connect(received.append)
    with qtbot.waitSignal(worker.heartbeat_sent, timeout=5000):
        worker.start()

    assert received == [True]
    assert len(client.heartbeat_calls) == 1
    call = client.heartbeat_calls[0]
    assert call["device_id"] == "DEV-001"
    assert call["app_version"] == "0.1.0"
    assert call["engine_version"] == "0.1.0"
    assert call["db_schema_version"] == "1"
    assert call["pending_sync_count"] == 7
    worker.stop()
    assert worker.wait(3000)


def test_heartbeat_failure_backoff_and_recovery(qtbot, tmp_path, clean_env):
    """心跳失败：heartbeat_sent(False) + 错误信号；退避后重试，恢复即复位退避。"""
    client = FakeApiClient(heartbeat_results=[None, {"device_id": "DEV-001"}])
    worker = _make_worker(client, tmp_path / "cache")
    worker._interval = 3600.0
    worker._backoff = 0.1  # 缩短退避等待，让恢复重试落在测试超时窗口内

    results: list[bool] = []
    errors: list[str] = []
    worker.heartbeat_sent.connect(results.append)
    worker.error_occurred.connect(errors.append)
    worker.start()

    qtbot.waitUntil(lambda: len(results) >= 2, timeout=5000)
    assert results == [False, True]
    assert len(errors) == 1
    # 恢复在线的那次心跳成功后退避复位为基数（60 秒）
    qtbot.waitUntil(lambda: worker._backoff == BACKOFF_BASE_SECONDS, timeout=5000)
    worker.stop()
    assert worker.wait(3000)


# --------------------------------------------------------------------------
# 2. 配置验签
# --------------------------------------------------------------------------


def test_config_verified_applied_and_cached(qtbot, tmp_path, clean_env):
    """验签通过：config_applied(version)，配置写入 agent_config.json 缓存。"""
    config = _signed_config("v2026.3")
    client = FakeApiClient(config=config)
    worker = _make_worker(client, tmp_path / "cache")
    worker._interval = 3600.0

    applied: list[str] = []
    rejected: list[str] = []
    worker.config_applied.connect(applied.append)
    worker.config_rejected.connect(rejected.append)
    with qtbot.waitSignal(worker.config_applied, timeout=5000):
        worker.start()

    assert applied == ["v2026.3"]
    assert rejected == []
    cache = worker.config_cache_path
    assert cache.exists()
    cached = json.loads(cache.read_text(encoding="utf-8"))
    assert cached["version"] == "v2026.3"
    assert cached["content"] == CONTENT
    worker.stop()
    assert worker.wait(3000)


def test_config_rejected_tampered_signature_keeps_old_cache(qtbot, tmp_path, clean_env):
    """验签失败（篡改签名）：config_rejected，旧缓存保留不被覆盖。"""
    config = _signed_config("v2")
    config["signature"] = "0" * 64
    old_cache_dir = tmp_path / "cache"
    old_cache_dir.mkdir(parents=True)
    (old_cache_dir / AGENT_CONFIG_FILENAME).write_text(
        json.dumps({"version": "v1-old", "content": {}}), encoding="utf-8"
    )
    client = FakeApiClient(config=config)
    worker = _make_worker(client, old_cache_dir)
    worker._interval = 3600.0

    applied: list[str] = []
    rejected: list[str] = []
    worker.config_applied.connect(applied.append)
    worker.config_rejected.connect(rejected.append)
    with qtbot.waitSignal(worker.config_rejected, timeout=5000):
        worker.start()

    assert applied == []
    assert len(rejected) == 1
    cached = json.loads(worker.config_cache_path.read_text(encoding="utf-8"))
    assert cached["version"] == "v1-old"
    worker.stop()
    assert worker.wait(3000)


def test_verify_config_payload_tampered_content() -> None:
    """摘要与签名校验单元：内容被篡改或签名不符均返回 False。"""
    config = _signed_config("v1")
    assert verify_config_payload(config, SECRET) is True

    tampered = dict(config)
    tampered["content"] = dict(CONTENT, heartbeat_interval=999)
    assert verify_config_payload(tampered, SECRET) is False

    wrong_secret = _signed_config("v1")
    assert verify_config_payload(wrong_secret, "other-secret") is False


# --------------------------------------------------------------------------
# 3. 任务拉取执行（占位）
# --------------------------------------------------------------------------


def test_tasks_pulled_and_executed(qtbot, tmp_path, clean_env):
    """任务执行：pull_tasks 返回 2 个任务，逐个发出 task_executed(task_id, ok)。"""
    tasks = [
        {"task": {"task_id": "TASK-001", "task_type": "SYNC"}, "run": {"run_id": "run-1"}},
        {"task": {"task_id": "TASK-002", "task_type": "REPORT"}, "run": {"run_id": "run-2"}},
    ]
    client = FakeApiClient(tasks=tasks)
    worker = _make_worker(client, tmp_path / "cache")
    worker._interval = 3600.0

    executed: list[tuple[str, bool]] = []
    worker.task_executed.connect(lambda task_id, ok: executed.append((task_id, ok)))
    with qtbot.waitSignal(worker.task_executed, timeout=5000):
        worker.start()

    qtbot.waitUntil(lambda: len(executed) == 2, timeout=5000)
    assert executed == [("TASK-001", True), ("TASK-002", True)]
    assert client.pull_calls == [("DEV-001", 10)]
    worker.stop()
    assert worker.wait(3000)


# --------------------------------------------------------------------------
# 4. stop() 与 device_id 注入
# --------------------------------------------------------------------------


def test_stop_interrupts_wait(qtbot, tmp_path, clean_env):
    """stop() 能中断心跳间隔等待，线程秒级退出。"""
    client = FakeApiClient()
    worker = _make_worker(client, tmp_path / "cache", interval=60.0)

    with qtbot.waitSignal(worker.heartbeat_sent, timeout=5000):
        worker.start()
    worker.stop()
    assert worker.wait(3000), "线程应在 stop() 后及时退出"


def test_set_device_id_before_start(tmp_path, clean_env):
    """组合根模式：注册成功后 set_device_id，再 start。"""
    client = FakeApiClient()
    worker = AgentWorker(client, device_id="", config_cache_dir=tmp_path / "cache")
    worker.set_device_id("DEV-9")
    assert client.pull_calls == []
    worker._pull_and_execute_tasks()
    assert client.pull_calls == [("DEV-9", 10)]


def test_sleep_backoff_progression(tmp_path, clean_env):
    """退避数值验证：60 → 120 → 240 → 480 → 600（上限）→ 600。"""
    worker = AgentWorker(
        FakeApiClient(),
        device_id="DEV-001",
        config_cache_dir=tmp_path / "cache",
    )
    seq: list[float] = []
    original_sleep = worker._sleep
    worker._sleep = lambda seconds: seq.append(seconds)
    for _ in range(6):
        worker._sleep_backoff()
    worker._sleep = original_sleep
    assert seq == [60.0, 120.0, 240.0, 480.0, 600.0, 600.0]
