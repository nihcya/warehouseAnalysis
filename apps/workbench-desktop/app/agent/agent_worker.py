"""工作台 Agent 后台线程（M3 Task 3）：心跳上报、配置验签与任务拉取执行。

周期闭环（每轮）：心跳上报 → 配置拉取验签 → 任务拉取执行 → 等待下一次心跳间隔。

- 心跳：默认 60 秒上报一次（可配置）；断网时指数退避（60 → 120 → 240 …，
  上限 600 秒），重试成功即复位退避并回归正常间隔；
- 配置：``GET /config`` 后先验内容 SHA-256 摘要，再验 HMAC-SHA256 签名
  （与云端 ``config_usecase`` 同一口径：``HMAC(secret, "version:sha256")``，
  密钥经 ``CONFIG_SIGNING_SECRET`` 下发）；验签通过写本地缓存
  ``%LOCALAPPDATA%/WarehouseWorkbench/agent_config.json``，失败保留旧缓存
  并发出 ``config_rejected`` 警告信号；
- 任务：``POST /tasks/pull`` 拉取已锁定（QUEUED）的任务，逐个执行。
  执行体当前为占位实现（记录日志并模拟 PENDING → RUNNING → SUCCEEDED/FAILED），
  失败重试由云端任务状态机负责。

线程模式与 ``StatusStreamWorker`` 一致：``run`` 循环轮询 ``_stop`` 标志，
所有睡眠均分步可中断，``stop()`` 后线程在秒级内退出。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal

#: 默认心跳间隔（秒）
DEFAULT_HEARTBEAT_INTERVAL = 60.0

#: 断网退避上限（秒）
MAX_BACKOFF_SECONDS = 600.0

#: 退避基数（秒）：失败一次 ×2，60 → 120 → 240 → 480 → 600
BACKOFF_BASE_SECONDS = DEFAULT_HEARTBEAT_INTERVAL

#: 默认配置签名密钥环境变量（云端 CONFIG_SIGNING_SECRET 同名下发）
SIGNING_SECRET_ENV = "CONFIG_SIGNING_SECRET"

#: 默认数据目录名（%LOCALAPPDATA% 下，与 TokenStore 同根）
APP_DATA_DIR_NAME = "WarehouseWorkbench"

#: 配置缓存文件名
AGENT_CONFIG_FILENAME = "agent_config.json"

#: 默认待执行任务单次拉取上限
DEFAULT_TASK_PULL_LIMIT = 10

#: 任务占位执行的模拟耗时（秒）
_TASK_EXECUTE_DELAY = 0.01

logger = logging.getLogger(__name__)


def canonical_config_bytes(content: dict[str, Any]) -> bytes:
    """配置内容规范化序列化（与云端 domain/config.py 唯一口径一致）。"""
    return json.dumps(
        content, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def config_signature(secret: str, version: str, sha256: str) -> str:
    """配置签名：HMAC-SHA256(secret, "version:sha256")（与云端同口径）。"""
    message = f"{version}:{sha256}".encode()
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_config_payload(config: dict[str, Any], secret: str) -> bool:
    """校验 GET /config 载荷：内容摘要与签名必须同时通过（恒时比较）。"""
    content = config.get("content")
    version = config.get("version")
    sha256 = config.get("sha256")
    signature = config.get("signature")
    if not isinstance(content, dict) or not isinstance(version, str):
        return False
    if not isinstance(sha256, str) or not isinstance(signature, str):
        return False
    digest = hashlib.sha256(canonical_config_bytes(content)).hexdigest()
    if not hmac.compare_digest(digest, sha256):
        return False
    return hmac.compare_digest(config_signature(secret, version, digest), signature)


class AgentWorker(QThread):
    """工作台 Agent 后台线程：心跳 / 配置 / 任务闭环（信号跨线程推送 UI）。"""

    #: 心跳上报完成（ok=True 成功；False 失败进入退避）
    heartbeat_sent = Signal(bool)
    #: 配置验签通过并已应用（参数为配置版本号）
    config_applied = Signal(str)
    #: 配置验签失败 / 载荷非法（参数为拒绝原因）
    config_rejected = Signal(str)
    #: 任务执行完成（ok=False 表示占位执行失败）
    task_executed = Signal(str, bool)
    #: 错误消息（网络不可达等）
    error_occurred = Signal(str)

    #: 默认心跳间隔（秒），可被构造参数覆盖
    _DEFAULT_INTERVAL = DEFAULT_HEARTBEAT_INTERVAL

    def __init__(
        self,
        api_client: Any,
        device_id: str,
        *,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        app_version: str = "0.1.0",
        engine_version: str = "0.1.0",
        schema_version: str = "1",
        signing_secret: str | None = None,
        config_cache_dir: Path | str | None = None,
        task_pull_limit: int = DEFAULT_TASK_PULL_LIMIT,
        pending_sync_provider: Callable[[], int] | None = None,
        status_provider: Callable[[], str] | None = None,
    ) -> None:
        """初始化 Agent 后台线程。

        :param api_client: 控制平面客户端（需提供 send_heartbeat / get_config /
            pull_tasks；duck typing，便于测试注入 fake）。
        :param device_id: 已注册设备标识（心跳与任务拉取载荷必填）。
        :param heartbeat_interval: 心跳间隔（秒），默认 60。
        :param app_version: 心跳载荷应用版本。
        :param engine_version: 心跳载荷引擎版本。
        :param schema_version: 心跳载荷本地库 schema 版本。
        :param signing_secret: 配置验签密钥；缺省读 ``CONFIG_SIGNING_SECRET``
            环境变量，与云端签发密钥一致。
        :param config_cache_dir: 配置缓存目录；缺省 ``%LOCALAPPDATA%``
            （无该环境变量回退用户主目录），测试传入 tmp 路径隔离。
        :param task_pull_limit: 单次任务拉取上限。
        :param pending_sync_provider: 待同步数提供者（每次心跳前调用）。
        :param status_provider: Agent 运行状态提供者（如 "RUNNING" / "IDLE"）。
        """
        super().__init__()
        self._api_client = api_client
        self._device_id = device_id
        self._interval = heartbeat_interval
        self._app_version = app_version
        self._engine_version = engine_version
        self._schema_version = schema_version
        self._signing_secret = (
            signing_secret if signing_secret is not None else os.environ.get(SIGNING_SECRET_ENV, "")
        )
        if config_cache_dir is None:
            base = os.environ.get("LOCALAPPDATA")
            root = Path(base) if base else Path.home()
            self._cache_path = root / APP_DATA_DIR_NAME / AGENT_CONFIG_FILENAME
        else:
            self._cache_path = Path(config_cache_dir) / AGENT_CONFIG_FILENAME
        self._task_pull_limit = task_pull_limit
        self._pending_sync_provider = pending_sync_provider
        self._status_provider = status_provider
        self._backoff = BACKOFF_BASE_SECONDS
        self._stop = False

    def stop(self) -> None:
        """请求线程退出（设置停止标志，run 循环将在下次检查时退出）。"""
        self._stop = True

    def set_device_id(self, device_id: str) -> None:
        """设置设备标识（组合根在设备注册成功后、start() 前调用）。"""
        self._device_id = device_id

    @property
    def config_cache_path(self) -> Path:
        """配置缓存文件路径（测试断言用）。"""
        return self._cache_path

    def load_cached_config(self) -> dict[str, Any] | None:
        """读取本地配置缓存；文件不存在或损坏时返回 None。"""
        if not self._cache_path.exists():
            return None
        try:
            data: dict[str, Any] = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def run(self) -> None:
        """线程主循环：心跳 → 配置 → 任务 → 等待，直到 stop()。"""
        while not self._stop:
            ok = self._beat()
            if self._stop:
                break
            if ok:
                self._backoff = BACKOFF_BASE_SECONDS
            self._refresh_config()
            if self._stop:
                break
            self._pull_and_execute_tasks()
            if self._stop:
                break
            if ok:
                self._sleep(self._interval)
            else:
                self._sleep_backoff()

    # ------------------------------------------------------------------
    # 心跳
    # ------------------------------------------------------------------

    def _beat(self) -> bool:
        """上报一次心跳；成功立即返回 True，失败退避并返回 False。"""
        payload_status = self._status_provider() if self._status_provider else "RUNNING"
        pending = self._pending_sync_provider() if self._pending_sync_provider else 0
        result = self._api_client.send_heartbeat(
            self._device_id,
            payload_status,
            app_version=self._app_version,
            engine_version=self._engine_version,
            db_schema_version=self._schema_version,
            pending_sync_count=pending,
        )
        if self._stop:
            return result is not None
        ok = result is not None
        self.heartbeat_sent.emit(ok)
        if not ok:
            self.error_occurred.emit("心跳上报失败：控制平面不可达")
        return ok

    def _sleep_backoff(self) -> None:
        """断网指数退避：按当前退避值等待，随后翻倍（60 → 120 → 240 …上限 600 秒）。

        等待结束后立即重试心跳（恢复在线的那次心跳即成功上报）；
        成功后由 ``run`` 循环把退避复位为基数。
        """
        self._sleep(self._backoff)
        if not self._stop:
            self._backoff = min(self._backoff * 2, MAX_BACKOFF_SECONDS)

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------

    def _refresh_config(self) -> None:
        """拉取云端配置并验签；通过则写缓存并广播，失败保留旧缓存告警。"""
        config = self._api_client.get_config()
        if self._stop:
            return
        if config is None:
            return
        if not isinstance(config, dict):
            self.config_rejected.emit("配置载荷非法：非对象结构")
            return
        secret = self._signing_secret
        if not secret:
            self.config_rejected.emit("配置验签失败：未配置签名密钥 CONFIG_SIGNING_SECRET")
            return
        if not verify_config_payload(config, secret):
            self.config_rejected.emit(f"配置验签失败：version={config.get('version')!r} 摘要或签名不符")
            return
        self._save_config_cache(config)
        version = config.get("version")
        self.config_applied.emit(str(version))

    def _save_config_cache(self, config: dict[str, Any]) -> None:
        """配置写本地缓存（自动创建目录；写失败仅记日志，不中断主循环）。"""
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("配置缓存写入失败：%s", exc)

    # ------------------------------------------------------------------
    # 任务
    # ------------------------------------------------------------------

    def _pull_and_execute_tasks(self) -> None:
        """拉取待执行任务并逐个执行（云端已锁定为 QUEUED，不重复分发）。"""
        pulled = self._api_client.pull_tasks(self._device_id, limit=self._task_pull_limit)
        if self._stop or not pulled:
            return
        for item in pulled:
            if self._stop:
                return
            task_id, ok = self._execute_task(item)
            self.task_executed.emit(task_id, ok)

    def _execute_task(self, item: dict[str, Any]) -> tuple[str, bool]:
        """执行单个任务（占位实现）：记录日志并模拟状态迁移。

        完整任务结果留在本地；失败重试交给云端任务状态机。
        """
        task = item.get("task") or {}
        run = item.get("run") or {}
        task_id = str(task.get("task_id", ""))
        run_id = str(run.get("run_id", ""))
        task_type = str(task.get("task_type", ""))
        logger.info("任务开始：task=%s run=%s type=%s", task_id, run_id, task_type)
        time.sleep(_TASK_EXECUTE_DELAY)
        logger.info("任务完成：task=%s run=%s", task_id, run_id)
        return task_id, True

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _sleep(self, seconds: float) -> None:
        """可被 stop 中断的等待（按 0.2 秒步长轮询 _stop 标志）。"""
        remaining = seconds
        while remaining > 0 and not self._stop:
            step = min(0.2, remaining)
            time.sleep(step)
            remaining -= step
