from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.schemas.health import HealthResponse
from app.services.redis import get_redis

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/live", response_model=HealthResponse, include_in_schema=False)
async def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse, include_in_schema=False)
async def readiness(
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    try:
        async with asyncio.timeout(settings.health_check_timeout_seconds):
            await session.execute(text("SELECT 1"))
            await redis.ping()
    except (TimeoutError, SQLAlchemyError, RedisError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A required service is unavailable.",
        ) from exc
    return HealthResponse(status="ok")
