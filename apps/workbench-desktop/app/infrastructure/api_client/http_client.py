"""HTTP API 客户端：连接控制平面的同步 httpx 实现。

实现 ``ApiClient`` 协议（``online`` 属性 + ``health`` 方法），
按 ``packages/contracts-schema/openapi.json`` 的 /api/v1/* 端点调用控制平面：

- 认证：login / refresh / logout（令牌自动持久化到 ``TokenStore``）；
- 账号：account/me（401 时自动刷新令牌重试一次）；
- 设备：register / list；
- 状态流：snapshot（轮询降级入口）+ stream_url（SSE 流地址，供后台线程使用）。

网络异常时设置 ``online=False``，方法返回 None（不抛异常），
保证 UI 状态栏只看到"离线"而不被未捕获异常打断。
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .token_store import TokenBundle, TokenStore

#: 默认控制平面地址
DEFAULT_BASE_URL = "http://localhost:8000"

#: 请求超时（秒）
REQUEST_TIMEOUT = 10.0

#: 演示账号登录名
DEMO_USERNAME = "merchant_demo"

#: 演示密码环境变量
DEMO_PASSWORD_ENV = "CONTROL_PLANE_DEMO_PASSWORD"

#: 离线占位健康响应（对齐 ErrorResponse 结构）
_OFFLINE_HEALTH: dict[str, Any] = {
    "error": {
        "code": "CLIENT_OFFLINE",
        "message": "离线模式：无法连接控制平面。",
        "request_id": "-",
    }
}


class HttpApiClient:
    """连接控制平面的 HTTP API 客户端（ApiClient 协议实现）。"""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        token_store: TokenStore | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        """初始化客户端。

        :param base_url: 控制平面基址。
        :param token_store: 令牌持久化仓储；缺省按 %LOCALAPPDATA% 落盘。
        :param client: 预构建的 httpx.Client；缺省按 base_url 与超时新建。
            测试可注入 Starlette TestClient（ASGI 桥接）或 MockTransport 客户端。
        """
        self._base_url = base_url
        self._token_store = token_store or TokenStore()
        self._client = client or httpx.Client(
            base_url=base_url,
            timeout=REQUEST_TIMEOUT,
        )
        self._online = False

    @property
    def online(self) -> bool:
        """是否在线：反映最后一次请求是否成功连接。"""
        return self._online

    @property
    def access_token(self) -> str | None:
        """当前 access_token（供 SSE 后台线程附加 Authorization 头）。"""
        bundle = self._token_store.load()
        return bundle.access_token if bundle is not None else None

    # ------------------------------------------------------------------
    # 内部请求
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        auth: bool = True,
        _retried: bool = False,
    ) -> httpx.Response | None:
        """发送带鉴权的请求；401 时自动刷新令牌重试一次，网络异常返回 None。"""
        headers: dict[str, str] = {}
        if auth:
            bundle = self._token_store.load()
            if bundle is not None:
                headers["Authorization"] = f"Bearer {bundle.access_token}"

        try:
            resp = self._client.request(method, path, headers=headers, json=json)
        except httpx.HTTPError:
            self._online = False
            return None
        self._online = True

        # 令牌过期 / 失效：尝试刷新后重试一次
        if resp.status_code == 401 and auth and not _retried and self.refresh_token():
            return self._request(method, path, json=json, auth=auth, _retried=True)
        return resp

    # ------------------------------------------------------------------
    # 认证
    # ------------------------------------------------------------------

    def login(self, username: str, password: str) -> dict[str, Any] | None:
        """POST /api/v1/auth/login：校验账号密码，返回 AuthData，自动保存令牌。"""
        resp = self._request(
            "POST",
            "/api/v1/auth/login",
            auth=False,
            json={"username": username, "password": password},
        )
        if resp is None or resp.status_code != 200:
            return None
        data = resp.json()["data"]
        self._save_tokens(data)
        return data

    def login_demo(self) -> dict[str, Any] | None:
        """用演示账号（merchant_demo）登录；密码取自 CONTROL_PLANE_DEMO_PASSWORD。"""
        password = os.environ.get(DEMO_PASSWORD_ENV, "")
        return self.login(DEMO_USERNAME, password)

    def refresh_token(self) -> bool:
        """POST /api/v1/auth/refresh：用 refresh_token 换新令牌对，成功返回 True。"""
        bundle = self._token_store.load()
        if bundle is None or not bundle.refresh_token:
            return False
        try:
            resp = self._client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": bundle.refresh_token},
            )
        except httpx.HTTPError:
            self._online = False
            return False
        self._online = True
        if resp.status_code != 200:
            return False
        self._save_tokens(resp.json()["data"])
        return True

    def logout(self) -> bool:
        """POST /api/v1/auth/logout：注销会话，清除本地令牌。"""
        resp = self._request("POST", "/api/v1/auth/logout")
        self._token_store.clear()
        return resp is not None and resp.status_code == 200

    # ------------------------------------------------------------------
    # 账号
    # ------------------------------------------------------------------

    def get_account_me(self) -> dict[str, Any] | None:
        """GET /api/v1/account/me：返回 AccountMeData；401 时自动刷新重试。"""
        resp = self._request("GET", "/api/v1/account/me")
        if resp is None or resp.status_code != 200:
            return None
        return resp.json()["data"]

    # ------------------------------------------------------------------
    # 设备
    # ------------------------------------------------------------------

    def register_device(
        self,
        name: str,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        """POST /api/v1/devices/register：注册设备（同一指纹幂等）。"""
        resp = self._request(
            "POST",
            "/api/v1/devices/register",
            json={"name": name, "fingerprint": fingerprint},
        )
        if resp is None or resp.status_code != 200:
            return None
        return resp.json()["data"]

    def list_devices(self) -> list[dict[str, Any]] | None:
        """GET /api/v1/devices：列出当前商户的全部设备。"""
        resp = self._request("GET", "/api/v1/devices")
        if resp is None or resp.status_code != 200:
            return None
        return resp.json()["data"]

    # ------------------------------------------------------------------
    # 状态流
    # ------------------------------------------------------------------

    def get_snapshot(self) -> dict[str, Any] | None:
        """GET /api/v1/events/snapshot：轮询降级入口的状态快照。"""
        resp = self._request("GET", "/api/v1/events/snapshot")
        if resp is None or resp.status_code != 200:
            return None
        return resp.json()["data"]

    def stream_url(self) -> str:
        """返回 SSE 流 URL（供后台线程使用）。"""
        return f"{self._base_url}/api/v1/events/stream"

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """GET /health：即使离线也返回占位信息。"""
        try:
            resp = self._client.get("/health")
            data = resp.json()
            self._online = True
            return data
        except (httpx.HTTPError, ValueError):
            # 网络不可达或响应非 JSON：视为离线，返回占位
            self._online = False
            return dict(_OFFLINE_HEALTH)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _save_tokens(self, data: dict[str, Any]) -> None:
        """从认证响应载荷提取令牌对并持久化。"""
        tokens = data["tokens"]
        bundle = TokenBundle(
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            server_url=self._base_url,
            expires_at=tokens.get("refresh_expires_at"),
        )
        self._token_store.save(bundle)

    def close(self) -> None:
        """关闭底层 HTTP 连接池。"""
        self._client.close()
