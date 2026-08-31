"""令牌签发与校验（M2 实装）。

Access Token：JWT（HS256），有效期 15 分钟（SECURITY.md），
载荷只放鉴权必需声明——账号、租户、角色、Scope、会话、设备，不放任何业务数据。

Refresh Token：不透明随机串，**只以 SHA-256 指纹持久化**（主基线 §35.5）；
轮换与重放判定由 ``app.domain.session.Session`` 负责，本模块只负责生成与取指纹。

所有校验失败路径返回 ``None``，由 api 层统一映射为 401 ``AUTH_REQUIRED``，
不区分"过期 / 签名错误 / 被篡改"，避免给攻击者提供有效反馈。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt

from app.domain.account import AccountRole
from app.domain.session import ACCESS_TOKEN_TTL, ClientType

#: JWT 签名算法（对称密钥，密钥由 Settings.resolve_auth_secret 提供）
JWT_ALGORITHM = "HS256"

#: Refresh Token 随机字节数（token_urlsafe 后约 43 字符）
REFRESH_TOKEN_BYTES = 32

#: JWT 声明键（简短键名减小令牌体积）
CLAIM_SUBJECT = "sub"
CLAIM_TENANT = "tid"
CLAIM_ROLE = "role"
CLAIM_SCOPES = "scopes"
CLAIM_SESSION = "sid"
CLAIM_DEVICE = "did"
CLAIM_ISSUED_AT = "iat"
CLAIM_EXPIRES = "exp"
#: 令牌唯一 ID（同一秒内重复签发也得到不同令牌，便于日志追踪与重放排查）
CLAIM_TOKEN_ID = "jti"


@dataclass(frozen=True)
class Principal:
    """通过鉴权的调用主体（由 Access Token 解析而来）。"""

    account_id: str
    tenant_id: str | None
    role: AccountRole
    scopes: frozenset[str]
    session_id: str
    device_id: str | None = None
    expires_at: datetime | None = None

    def has_scope(self, scope: str) -> bool:
        """是否拥有指定 Scope（空租户的开发者账号仍按 Scope 授权）。"""
        return scope in self.scopes


def issue_access_token(
    *,
    account_id: str,
    tenant_id: str | None,
    role: AccountRole,
    scopes: frozenset[str],
    session_id: str,
    device_id: str | None,
    secret: str,
    issued_at: datetime,
) -> tuple[str, datetime]:
    """签发 Access Token，返回 ``(token, expires_at)``。

    ``issued_at`` 由调用方注入（便于测试固定时间），过期时间按
    ``domain.session.ACCESS_TOKEN_TTL`` 推算。
    """
    expires_at = issued_at + ACCESS_TOKEN_TTL
    payload = {
        CLAIM_SUBJECT: account_id,
        CLAIM_TENANT: tenant_id,
        CLAIM_ROLE: role.value,
        CLAIM_SCOPES: sorted(scopes),
        CLAIM_SESSION: session_id,
        CLAIM_DEVICE: device_id,
        CLAIM_ISSUED_AT: int(issued_at.timestamp()),
        CLAIM_EXPIRES: int(expires_at.timestamp()),
        CLAIM_TOKEN_ID: uuid.uuid4().hex,
    }
    token = jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)
    return token, expires_at


def decode_access_token(token: str, secret: str) -> Principal | None:
    """解析 Access Token；无效/过期/被篡改一律返回 None（不区分原因）。"""
    try:
        payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None

    account_id = payload.get(CLAIM_SUBJECT)
    session_id = payload.get(CLAIM_SESSION)
    role_value = payload.get(CLAIM_ROLE)
    if not isinstance(account_id, str) or not account_id:
        return None
    if not isinstance(session_id, str) or not session_id:
        return None
    try:
        role = AccountRole(role_value)
    except ValueError:
        return None

    tenant_id = payload.get(CLAIM_TENANT)
    device_id = payload.get(CLAIM_DEVICE)
    raw_scopes = payload.get(CLAIM_SCOPES) or []
    expires_at = payload.get(CLAIM_EXPIRES)

    return Principal(
        account_id=account_id,
        tenant_id=tenant_id if isinstance(tenant_id, str) else None,
        role=role,
        scopes=frozenset(scope for scope in raw_scopes if isinstance(scope, str)),
        session_id=session_id,
        device_id=device_id if isinstance(device_id, str) else None,
        expires_at=(
            datetime.fromtimestamp(expires_at, tz=UTC)
            if isinstance(expires_at, (int, float))
            else None
        ),
    )


def new_refresh_token() -> str:
    """生成 URL 安全的随机 Refresh Token（明文只在签发响应中出现一次）。"""
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def refresh_token_hash(token: str) -> str:
    """Refresh Token 的 SHA-256 指纹（持久化与比对只用指纹，不存明文）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_client_type_of(value: str, default: ClientType = ClientType.WEB) -> ClientType:
    """把请求中的客户端类型字符串解析为枚举；未知取值回退默认值。"""
    try:
        return ClientType(value)
    except ValueError:
        return default
