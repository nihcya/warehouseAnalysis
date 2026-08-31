"""认证用例（M2）：登录、刷新令牌轮换、注销与当前账号查询。

设计要点：

- **失败信息不区分**：账号不存在与密码错误返回同一文案，避免账号枚举；
- **Refresh Token 轮换**：每次刷新签发新令牌、旧指纹转为 previous；
  使用 previous 刷新判定为重放，吊销该账号全部会话（SECURITY.md）；
- **许可证不阻断登录**：登录只返回评估结果，由受保护端点按需拒绝
  （``app/api/v1/deps.py`` 的商户依赖），保证用户能看到"已过期"而不是登录失败；
- **租户挂起阻断登录**：``tenant.status = SUSPENDED`` 直接拒绝（§35.6）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.application.audit import AuditService
from app.application.errors import auth_forbidden, auth_required
from app.application.license_usecase import EntitlementService
from app.application.ports import EventPublisher, IdentityRepository
from app.domain.account import Account
from app.domain.audit import AuditAction, AuditResult
from app.domain.license import LicenseEntitlement
from app.domain.session import ClientType, Session, refresh_expiry
from app.domain.tenant import Tenant, TenantStatus
from app.infrastructure.auth.passwords import verify_password
from app.infrastructure.auth.tokens import (
    Principal,
    issue_access_token,
    new_refresh_token,
    refresh_token_hash,
)
from app.infrastructure.ids import PREFIX_SESSION, new_id

#: 状态流事件类型：账号登录（Web 状态看板消费）
EVENT_AUTH_LOGIN = "auth.login"

#: 刷新重放的原因码（审计与错误 details 共用）
REASON_REFRESH_REUSED = "REFRESH_TOKEN_REUSED"


@dataclass(frozen=True)
class TokenPair:
    """签发给客户端的令牌对。"""

    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int  # Access Token 剩余秒数
    refresh_expires_at: datetime


@dataclass(frozen=True)
class AccountContext:
    """当前账号上下文：账号、商户与许可证评估结果。"""

    account: Account
    tenant: Tenant | None
    entitlement: LicenseEntitlement


@dataclass(frozen=True)
class AuthResult:
    """认证结果：令牌对 + 账号上下文。"""

    tokens: TokenPair
    context: AccountContext


class AuthService:
    """认证应用服务。"""

    def __init__(
        self,
        identity: IdentityRepository,
        entitlements: EntitlementService,
        audit: AuditService,
        *,
        secret: str,
        publisher: EventPublisher | None = None,
    ) -> None:
        self._identity = identity
        self._entitlements = entitlements
        self._audit = audit
        self._secret = secret
        self._publisher = publisher

    # ---- 登录 ----
    def login(
        self,
        *,
        login_name: str,
        password: str,
        client_type: ClientType = ClientType.WEB,
        device_id: str | None = None,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> AuthResult:
        """校验凭证并签发令牌对；失败抛应用层错误。"""
        now = now or datetime.now(UTC)
        account = self._identity.get_account_by_login_name(login_name)

        if account is None:
            _ = password  # 不校验，避免通过耗时差异枚举账号
            self._audit.record(
                action=AuditAction.AUTH_LOGIN,
                result=AuditResult.DENIED,
                occurred_at=now,
                target_type="account",
                target_id=login_name,
                request_id=request_id,
                detail={"reason": "ACCOUNT_NOT_FOUND"},
            )
            raise auth_required("账号或密码错误。")

        if not account.can_login(now):
            self._audit.record(
                action=AuditAction.AUTH_LOGIN,
                result=AuditResult.DENIED,
                occurred_at=now,
                actor_account_id=account.account_id,
                actor_role=account.role.value,
                tenant_id=account.tenant_id,
                target_type="account",
                target_id=account.account_id,
                request_id=request_id,
                detail={"reason": "ACCOUNT_UNAVAILABLE", "account_status": account.status.value},
            )
            raise auth_forbidden(
                "账号处于锁定或停用状态，无法登录。",
                reason="ACCOUNT_UNAVAILABLE",
                account_status=account.status.value,
            )

        if not verify_password(password, account.password_hash):
            account.register_failure(now)
            self._identity.save_account(account)
            self._audit.record(
                action=AuditAction.AUTH_LOGIN,
                result=AuditResult.DENIED,
                occurred_at=now,
                actor_account_id=account.account_id,
                actor_role=account.role.value,
                tenant_id=account.tenant_id,
                target_type="account",
                target_id=account.account_id,
                request_id=request_id,
                detail={"reason": "BAD_CREDENTIALS"},
            )
            raise auth_required("账号或密码错误。")

        tenant = (
            self._identity.get_tenant(account.tenant_id) if account.tenant_id else None
        )
        if tenant is not None and tenant.status is TenantStatus.SUSPENDED:
            self._audit.record(
                action=AuditAction.AUTH_LOGIN,
                result=AuditResult.DENIED,
                occurred_at=now,
                actor_account_id=account.account_id,
                actor_role=account.role.value,
                tenant_id=tenant.tenant_id,
                target_type="tenant",
                target_id=tenant.tenant_id,
                request_id=request_id,
                detail={"reason": "TENANT_SUSPENDED"},
            )
            raise auth_forbidden(
                "商户已暂停服务，请联系服务商。",
                reason="TENANT_SUSPENDED",
                tenant_id=tenant.tenant_id,
            )

        account.register_success(now)
        self._identity.save_account(account)

        session, refresh_token = self._new_session(
            account_id=account.account_id,
            client_type=client_type,
            device_id=device_id,
            now=now,
        )
        tokens = self._issue_tokens(
            account,
            session,
            refresh_token=refresh_token,
            device_id=device_id,
            now=now,
        )
        entitlement = self._entitlements.evaluate(account.tenant_id)

        self._audit.record(
            action=AuditAction.AUTH_LOGIN,
            result=AuditResult.SUCCESS,
            occurred_at=now,
            actor_account_id=account.account_id,
            actor_role=account.role.value,
            tenant_id=account.tenant_id,
            target_type="session",
            target_id=session.session_id,
            request_id=request_id,
            detail={"client_type": client_type.value},
        )
        if self._publisher is not None:
            self._publisher.publish(
                account.tenant_id,
                EVENT_AUTH_LOGIN,
                {
                    "account_id": account.account_id,
                    "client_type": client_type.value,
                    "device_id": device_id,
                    "license_status": entitlement.status.value,
                },
            )

        return AuthResult(
            tokens=tokens,
            context=AccountContext(account=account, tenant=tenant, entitlement=entitlement),
        )

    # ---- 刷新 ----
    def refresh(
        self,
        *,
        refresh_token: str,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> AuthResult:
        """轮换 Refresh Token 并重签 Access Token；重放即吊销全部会话。"""
        now = now or datetime.now(UTC)
        token_hash = refresh_token_hash(refresh_token)

        session = self._identity.get_session_by_refresh_hash(token_hash)
        if session is not None:
            if not session.is_active(now):
                self._audit.record(
                    action=AuditAction.AUTH_REFRESH,
                    result=AuditResult.DENIED,
                    occurred_at=now,
                    actor_account_id=session.account_id,
                    target_type="session",
                    target_id=session.session_id,
                    request_id=request_id,
                    detail={"reason": "SESSION_INACTIVE"},
                )
                raise auth_required("会话已失效，请重新登录。")
            return self._rotate(session, request_id=request_id, now=now)

        replayed = self._identity.get_session_by_previous_refresh_hash(token_hash)
        if replayed is not None:
            revoked = self._identity.revoke_sessions_of_account(replayed.account_id, now)
            replayed_account = self._identity.get_account(replayed.account_id)
            self._audit.record(
                action=AuditAction.AUTH_REFRESH,
                result=AuditResult.DENIED,
                occurred_at=now,
                actor_account_id=replayed.account_id,
                actor_role=replayed_account.role.value if replayed_account else None,
                tenant_id=replayed_account.tenant_id if replayed_account else None,
                target_type="session",
                target_id=replayed.session_id,
                request_id=request_id,
                detail={"reason": REASON_REFRESH_REUSED},
            )
            raise auth_required(
                "检测到刷新令牌被重复使用，已撤销全部会话，请重新登录。",
                reason=REASON_REFRESH_REUSED,
                revoked_sessions=revoked,
            )

        raise auth_required("刷新令牌无效。")

    # ---- 注销 ----
    def logout(
        self,
        *,
        principal: Principal,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """撤销当前会话；重复注销幂等成功。"""
        now = now or datetime.now(UTC)
        session = self._identity.get_session(principal.session_id)
        if session is not None and session.revoked_at is None:
            session.revoke(now)
            self._identity.save_session(session)
        self._audit.record(
            action=AuditAction.AUTH_LOGOUT,
            result=AuditResult.SUCCESS,
            occurred_at=now,
            actor_account_id=principal.account_id,
            actor_role=principal.role.value,
            tenant_id=principal.tenant_id,
            target_type="session",
            target_id=principal.session_id,
            request_id=request_id,
        )

    # ---- 当前账号 ----
    def current_context(self, *, principal: Principal) -> AccountContext:
        """按令牌主体查询账号、商户与许可证评估结果。"""
        account = self._identity.get_account(principal.account_id)
        if account is None:
            raise auth_required("账号不存在或已删除。")
        tenant = (
            self._identity.get_tenant(account.tenant_id) if account.tenant_id else None
        )
        return AccountContext(
            account=account,
            tenant=tenant,
            entitlement=self._entitlements.evaluate(account.tenant_id),
        )

    # ---- 内部 ----
    def _new_session(
        self,
        *,
        account_id: str,
        client_type: ClientType,
        device_id: str | None,
        now: datetime,
    ) -> tuple[Session, str]:
        """建立会话并发放首个 Refresh Token，返回 ``(session, 明文令牌)``。

        明文只在这一次返回中出现，持久化的是 SHA-256 指纹。
        """
        refresh_token = new_refresh_token()
        session = Session(
            session_id=new_id(PREFIX_SESSION),
            account_id=account_id,
            client_type=client_type,
            refresh_token_hash=refresh_token_hash(refresh_token),
            expires_at=refresh_expiry(now),
            device_id=device_id,
            created_at=now,
        )
        self._identity.add_session(session)
        return session, refresh_token

    def _rotate(
        self,
        session: Session,
        *,
        request_id: str | None,
        now: datetime,
    ) -> AuthResult:
        """轮换会话的 Refresh Token 并重签 Access Token。"""
        new_token = new_refresh_token()
        session.rotate(refresh_token_hash(new_token), now)
        session.expires_at = refresh_expiry(now)
        self._identity.save_session(session)

        account = self._identity.get_account(session.account_id)
        if account is None:
            raise auth_required("账号不存在或已删除。")
        tenant = (
            self._identity.get_tenant(account.tenant_id) if account.tenant_id else None
        )

        tokens = self._issue_tokens(
            account,
            session,
            refresh_token=new_token,
            device_id=session.device_id,
            now=now,
        )
        self._audit.record(
            action=AuditAction.AUTH_REFRESH,
            result=AuditResult.SUCCESS,
            occurred_at=now,
            actor_account_id=account.account_id,
            actor_role=account.role.value,
            tenant_id=account.tenant_id,
            target_type="session",
            target_id=session.session_id,
            request_id=request_id,
            detail={"client_type": session.client_type.value},
        )
        return AuthResult(
            tokens=tokens,
            context=AccountContext(
                account=account,
                tenant=tenant,
                entitlement=self._entitlements.evaluate(account.tenant_id),
            ),
        )

    def _issue_tokens(
        self,
        account: Account,
        session: Session,
        *,
        refresh_token: str,
        device_id: str | None,
        now: datetime,
    ) -> TokenPair:
        """签发 Access Token，并连同 Refresh Token 明文一起返回给调用方。"""
        access_token, expires_at = issue_access_token(
            account_id=account.account_id,
            tenant_id=account.tenant_id,
            role=account.role,
            scopes=account.scopes(),
            session_id=session.session_id,
            device_id=device_id,
            secret=self._secret,
            issued_at=now,
        )
        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="Bearer",
            expires_in=max(int((expires_at - now).total_seconds()), 0),
            refresh_expires_at=session.expires_at,
        )
