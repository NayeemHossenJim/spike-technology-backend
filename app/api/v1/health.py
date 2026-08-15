from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.metrics import (
    PROMETHEUS_CONTENT_TYPE,
    metrics_registry,
    render_report_processing_metrics,
)
from app.db.session import get_session
from app.schemas.health import HealthResponse
from app.services.processing_metrics import load_report_processing_metrics
from app.services.redis import get_redis

router = APIRouter(prefix="/health", tags=["Health"])
logger = logging.getLogger(__name__)


@router.get("/live", response_model=HealthResponse, include_in_schema=False)
async def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


def _require_metrics_access(
    settings: Settings,
    authorization: str | None,
) -> None:
    configured = settings.metrics_bearer_token

    if configured is None:
        return

    supplied = ""
    if authorization:
        scheme, separator, credentials = authorization.partition(" ")
        if separator and scheme.lower() == "bearer":
            supplied = credentials.strip()

    expected = configured.get_secret_value()

    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found.",
        )


@router.get("/metrics", include_in_schema=False)
async def operational_metrics(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[
        str | None,
        Header(alias="Authorization"),
    ] = None,
) -> Response:
    _require_metrics_access(
        settings,
        authorization,
    )

    processing_snapshot = None

    try:
        async with asyncio.timeout(settings.health_check_timeout_seconds):
            processing_snapshot = await load_report_processing_metrics(
                session=session,
            )
    except (TimeoutError, SQLAlchemyError) as exc:
        logger.warning(
            "report_processing_metrics_unavailable",
            extra={
                "outcome": "unavailable",
                "exception_type": type(exc).__name__,
                "phase": "metrics",
            },
        )

    content = metrics_registry.render_prometheus() + render_report_processing_metrics(
        processing_snapshot
    )

    return Response(
        content=content,
        media_type=PROMETHEUS_CONTENT_TYPE,
    )


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
