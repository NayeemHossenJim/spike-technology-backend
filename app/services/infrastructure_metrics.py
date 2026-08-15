from __future__ import annotations

import asyncio
import logging
from time import perf_counter

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import InfrastructureMetricsSnapshot

logger = logging.getLogger(__name__)


async def load_infrastructure_metrics(
    *,
    session: AsyncSession,
    redis: Redis,
    timeout_seconds: float,
) -> InfrastructureMetricsSnapshot:
    database_started = perf_counter()
    postgresql_available = False

    try:
        async with asyncio.timeout(timeout_seconds):
            await session.execute(text("SELECT 1"))
        postgresql_available = True
    except (TimeoutError, SQLAlchemyError) as exc:
        logger.warning(
            "postgresql_metrics_probe_failed",
            extra={
                "outcome": "unavailable",
                "exception_type": type(exc).__name__,
                "phase": "metrics",
            },
        )

    postgresql_probe_duration_seconds = max(
        0.0,
        perf_counter() - database_started,
    )

    redis_started = perf_counter()
    redis_available = False

    try:
        async with asyncio.timeout(timeout_seconds):
            redis_available = bool(await redis.ping())
    except (TimeoutError, RedisError) as exc:
        logger.warning(
            "redis_metrics_probe_failed",
            extra={
                "outcome": "unavailable",
                "exception_type": type(exc).__name__,
                "phase": "metrics",
            },
        )

    redis_probe_duration_seconds = max(
        0.0,
        perf_counter() - redis_started,
    )

    return InfrastructureMetricsSnapshot(
        postgresql_available=postgresql_available,
        postgresql_probe_duration_seconds=postgresql_probe_duration_seconds,
        redis_available=redis_available,
        redis_probe_duration_seconds=redis_probe_duration_seconds,
    )


__all__ = [
    "load_infrastructure_metrics",
]
