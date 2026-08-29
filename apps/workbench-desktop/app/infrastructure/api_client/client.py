"""云 API 客户端离线占位（§7.8）：依据 packages/contracts-schema/openapi.json 骨架。

M0 不实装任何网络调用：``OfflineApiClient`` 恒为离线，
``health`` 仅返回离线占位信息，供状态栏与后续同步链路展示。
"""

from __future__ import annotations

from typing import Any


class OfflineApiClient:
    """离线占位客户端：online 恒为 False，调用不产生任何网络请求。"""

    @property
    def online(self) -> bool:
        """离线模式（M0 无云连接）。"""
        return False

    def health(self) -> dict[str, Any]:
        """GET /health 的离线占位响应（错误体对齐 openapi.json 的 ErrorResponse 结构）。"""
        return {
            "error": {
                "code": "CLIENT_OFFLINE",
                "message": "离线模式：云 API 客户端为 M0 占位，未实装网络调用。",
                "request_id": "-",
            }
        }
