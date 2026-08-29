"""云 API 客户端端口：展示层状态栏与后续同步链路依赖的在线状态抽象。

M0 只有离线占位实现（infrastructure/api_client），真实 HTTP 客户端
依据 packages/contracts-schema/openapi.json 在 M1+ 实装。
"""

from __future__ import annotations

from typing import Any, Protocol


class ApiClient(Protocol):
    """云端控制面客户端端口（对齐 openapi.json 的 /health 与 /api/v1/*）。"""

    @property
    def online(self) -> bool:
        """是否在线；离线时 UI 仅展示离线状态，不发起任何请求。"""
        ...

    def health(self) -> dict[str, Any]:
        """GET /health 的占位方法（M0 不实装网络调用）。"""
        ...
