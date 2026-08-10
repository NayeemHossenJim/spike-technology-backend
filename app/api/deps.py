from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.config import Settings, get_settings
from app.core.security import TokenType, decode_jwt_token
from app.db.session import get_session
from app.models.ai import AIMessage
from app.models.business import TenantRole
from app.models.user import User, UserRole
from app.services.ai_credits import digest_ai_idempotency_key
from app.services.auth import AuthService
from app.services.email import EmailSender, get_email_sender
from app.services.rate_limit import enforce_ai_rate_limit, enforce_auth_rate_limit
from app.services.redis import get_redis
from app.services.tenant import (
    TenantAccessForbiddenError,
    TenantContext,
    TenantContextMissingError,
    TenantIntegrityError,
    load_tenant_context,
)

bearer_scheme = HTTPBearer(auto_error=False)


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    email_sender: Annotated[EmailSender, Depends(get_email_sender)],
) -> AuthService:
    return AuthService(session=session, settings=settings, email_sender=email_sender)


async def enforce_auth_rate_limit_dependency(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    try:
        redis = await get_redis()
        await enforce_auth_rate_limit(request=request, redis=redis, settings=settings)
    except RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable.",
        ) from exc


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not credentials or credentials.scheme.lower() != "bearer":
        raise credentials_exception

    try:
        token = decode_jwt_token(credentials.credentials, settings)
    except ValueError as exc:
        raise credentials_exception from exc
    if token.token_type is not TokenType.ACCESS:
        raise credentials_exception

    result = await session.execute(select(User).where(User.id == token.user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or token.session_version != user.auth_session_version:
        raise credentials_exception
    return user


def require_roles(*roles: UserRole) -> Callable[..., User]:
    async def role_guard(current_user: Annotated[User, Depends(get_current_user)]) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action.",
            )
        return current_user

    return role_guard


async def get_current_tenant(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantContext:
    try:
        return await load_tenant_context(session, current_user)
    except TenantAccessForbiddenError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform roles cannot access business financial data.",
        ) from exc
    except TenantContextMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Business onboarding is required.",
        ) from exc
    except TenantIntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The business assignment is invalid.",
        ) from exc


async def enforce_ai_rate_limit_dependency(
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=255,
            pattern=r"^[!-~]+$",
        ),
    ],
    current_user: Annotated[User, Depends(get_current_user)],
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    # Keep scalar identifiers before ending the read-only database transaction.
    business_id = tenant.scope.business_id
    user_id = current_user.id
    digest = digest_ai_idempotency_key(idempotency_key)
    existing = (
        await session.execute(
            tenant.scope.select(
                AIMessage,
                AIMessage.idempotency_key_digest == digest,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await session.commit()
        return

    # This dependency only performed a SELECT. Commit closes the transaction
    # without expiring objects because the project sessionmaker uses
    # expire_on_commit=False. A rollback would expire tenant/user instances.
    await session.commit()

    try:
        redis = await get_redis()
        await enforce_ai_rate_limit(
            redis=redis,
            settings=settings,
            business_id=business_id,
            user_id=user_id,
        )
    except RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI service is temporarily unavailable.",
        ) from exc


def require_tenant_roles(*roles: TenantRole) -> Callable[..., TenantContext]:
    async def role_guard(
        tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    ) -> TenantContext:
        if tenant.role_assignment.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this business action.",
            )
        return tenant

    return role_guard
