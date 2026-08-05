from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis

from app.core.config import Settings


async def enforce_auth_rate_limit(
    *,
    request: Request,
    redis: Redis,
    settings: Settings,
) -> None:
    """Redis fixed-window limit for credential and email-token endpoints."""

    client_host = request.client.host if request.client else "unknown"
    fingerprint = hashlib.sha256(client_host.encode("utf-8")).hexdigest()
    key = f"spike:rate-limit:auth:{fingerprint}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, settings.auth_rate_limit_window_seconds)
    if count > settings.auth_rate_limit_requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(settings.auth_rate_limit_window_seconds)},
        )


async def enforce_ai_rate_limit(
    *,
    redis: Redis,
    settings: Settings,
    business_id: UUID,
    user_id: UUID,
) -> None:
    """Tenant-and-user fixed-window limit for expensive AI generation requests."""

    fingerprint = hashlib.sha256(f"{business_id}:{user_id}".encode()).hexdigest()
    key = f"spike:rate-limit:ai:{fingerprint}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, settings.ai_rate_limit_window_seconds)
    if count > settings.ai_rate_limit_requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many AI requests. Please try again later.",
            headers={"Retry-After": str(settings.ai_rate_limit_window_seconds)},
        )
