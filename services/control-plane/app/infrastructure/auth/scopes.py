"""Bearer 令牌解析与 Scope 判定（M2 实装，替代 M0 dev token）。

M0 曾以 ``Bearer merchant`` 这类明文字符串冒充令牌，仅用于打通 Scope 依赖；
M2 起改为校验真实 JWT：

- 无 / 非 Bearer / 空令牌 → ``None``，由 api 层映射为 401 ``AUTH_REQUIRED``；
- 过期、签名错误、被篡改、声明缺失 → ``None``（不区分原因，避免有效反馈）；
- 解析成功返回 ``Principal``，Scope 不足由 api 层映射为 403 ``AUTH_FORBIDDEN``。

本模块只做"字符串 → Principal"的解析，不引入 FastAPI 依赖，便于单元测试。
"""

from __future__ import annotations

from app.infrastructure.auth.tokens import Principal, decode_access_token

BEARER_SCHEME = "bearer"


def bearer_token(authorization: str | None) -> str | None:
    """从 Authorization 头取出 Bearer 令牌；无有效令牌返回 None。"""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != BEARER_SCHEME:
        return None
    token = token.strip()
    return token or None


def principal_from_authorization(authorization: str | None, secret: str) -> Principal | None:
    """解析 Authorization 头为 Principal；任何无效情形均返回 None。"""
    token = bearer_token(authorization)
    if token is None:
        return None
    return decode_access_token(token, secret)
