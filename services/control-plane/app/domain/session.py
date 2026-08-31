"""登录会话（session）领域对象（M2 实装）。

模型：**一次登录一行**。刷新时轮换 Refresh Token——新指纹覆盖
``refresh_token_hash``，旧指纹移入 ``previous_refresh_token_hash``：

- 用 ``refresh_token_hash`` 刷新 → 正常轮换；
- 用 ``previous_refresh_token_hash`` 刷新 → 判定为重放（令牌可能已泄露），
  必须由应用层吊销该账号的全部会话（SECURITY.md：Refresh 轮换且只存哈希/指纹，可撤销）；
- 其它值 → 视为无效令牌，返回 401 ``AUTH_REQUIRED``。

会话本身只保存哈希与时间戳，不保存令牌原文；TTL 与签发由
``app.infrastructure.auth.tokens`` 负责。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

#: Access Token 有效期（SECURITY.md：15 分钟）
ACCESS_TOKEN_TTL = timedelta(minutes=15)

#: Refresh Token 有效期（自签发起算）
REFRESH_TOKEN_TTL = timedelta(days=30)


class ClientType(str, Enum):
    """会话客户端类型（与设备类型同词表，主基线 §7.2）。"""

    DESKTOP = "DESKTOP"  # 桌面工作台
    WEB = "WEB"  # Web 会话
    MINI_PROGRAM = "MINI_PROGRAM"  # 小程序


@dataclass
class Session:
    """登录会话实体：Refresh Token 指纹、轮换痕迹与撤销状态。"""

    session_id: str
    account_id: str
    client_type: ClientType
    refresh_token_hash: str
    expires_at: datetime
    device_id: str | None = None
    previous_refresh_token_hash: str | None = None
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime | None = None

    def is_active(self, now: datetime) -> bool:
        """会话是否有效：未撤销且未过期。"""
        return self.revoked_at is None and now < self.expires_at

    def matches(self, token_hash: str) -> bool:
        """给定指纹是否为当前有效 Refresh Token。"""
        return token_hash == self.refresh_token_hash

    def matches_previous(self, token_hash: str) -> bool:
        """给定指纹是否为刚被轮换掉的上一个 Refresh Token（重放判定）。"""
        return (
            self.previous_refresh_token_hash is not None
            and token_hash == self.previous_refresh_token_hash
        )

    def rotate(self, new_token_hash: str, now: datetime) -> None:
        """轮换 Refresh Token：旧指纹转为 previous，新指纹生效。"""
        self.previous_refresh_token_hash = self.refresh_token_hash
        self.refresh_token_hash = new_token_hash
        self.rotated_at = now

    def revoke(self, now: datetime) -> None:
        """撤销会话（终态，不可恢复）。"""
        self.revoked_at = now


def refresh_expiry(issued_at: datetime) -> datetime:
    """按 Refresh Token TTL 计算过期时间。"""
    return issued_at + REFRESH_TOKEN_TTL


def access_expiry(issued_at: datetime) -> datetime:
    """按 Access Token TTL 计算过期时间。"""
    return issued_at + ACCESS_TOKEN_TTL
