"""Scope 令牌解析占位（M0）。

M0 以极简 dev token 代替真实认证：Authorization header 形如
``Bearer <scopes>``（scopes 以空格分隔，如 ``Bearer merchant``、
``Bearer developer``）。JWT / Argon2id / token rotation 属 M2 交付，
届时仅替换本模块实现，api 层依赖（app/api/v1/deps.py）签名保持不变。
"""

from __future__ import annotations


def parse_bearer_scopes(authorization: str | None) -> list[str] | None:
    """解析 Bearer dev token 携带的 scope 列表。

    返回 None 表示无有效凭证（header 缺失、非 Bearer 前缀或 token 为空），
    由 api 层映射为 401 ``AUTH_REQUIRED``。
    """
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.split()
