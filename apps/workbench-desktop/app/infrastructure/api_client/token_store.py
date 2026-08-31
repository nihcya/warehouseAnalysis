"""令牌对持久化：把 access_token / refresh_token / server_url 存到本地 JSON 文件。

默认存放在 ``%LOCALAPPDATA%\\WarehouseWorkbench\\auth.json``；
构造函数接受 ``data_dir`` 参数用于测试隔离（绝不触碰真实 %LOCALAPPDATA%）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: 默认数据目录名（%LOCALAPPDATA% 下）
APP_DATA_DIR_NAME = "WarehouseWorkbench"

#: 令牌文件名
AUTH_FILENAME = "auth.json"


@dataclass(frozen=True, slots=True)
class TokenBundle:
    """令牌对与服务地址的不可变值对象。"""

    access_token: str
    refresh_token: str
    server_url: str
    #: Refresh Token 过期时间（ISO 8601 字符串，登录响应里的 refresh_expires_at）
    expires_at: str | None = None


class TokenStore:
    """令牌对持久化：读写本地 JSON 文件。"""

    def __init__(self, data_dir: Path | str | None = None) -> None:
        """初始化令牌存储路径。

        :param data_dir: 数据目录；缺省取 ``%LOCALAPPDATA%\\WarehouseWorkbench``，
            测试传入 tmp 路径以隔离真实用户目录。
        """
        if data_dir is None:
            base = os.environ.get("LOCALAPPDATA")
            root = Path(base) if base else Path.home()
            self._data_dir = root / APP_DATA_DIR_NAME
        else:
            self._data_dir = Path(data_dir)
        self._path = self._data_dir / AUTH_FILENAME

    @property
    def path(self) -> Path:
        """令牌文件路径。"""
        return self._path

    def load(self) -> TokenBundle | None:
        """读取令牌对；文件不存在或损坏时返回 None。"""
        if not self._path.exists():
            return None
        try:
            data: dict[str, Any] = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        if not access_token or not refresh_token:
            return None
        return TokenBundle(
            access_token=access_token,
            refresh_token=refresh_token,
            server_url=data.get("server_url", ""),
            expires_at=data.get("expires_at"),
        )

    def save(self, bundle: TokenBundle) -> None:
        """写入令牌对（自动创建目录）。"""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "access_token": bundle.access_token,
            "refresh_token": bundle.refresh_token,
            "server_url": bundle.server_url,
            "expires_at": bundle.expires_at,
        }
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def clear(self) -> None:
        """删除令牌文件（不存在时静默）。"""
        if self._path.exists():
            self._path.unlink()
