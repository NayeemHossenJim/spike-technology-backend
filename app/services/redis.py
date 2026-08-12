from __future__ import annotations

from redis.asyncio import Redis, from_url

from app.core.config import Settings, get_settings


def create_redis_client(settings: Settings) -> Redis:
    return from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
        health_check_interval=30,
    )


settings = get_settings()
redis_client: Redis = create_redis_client(settings)


async def get_redis() -> Redis:
    return redis_client


async def close_redis() -> None:
    await redis_client.aclose()
