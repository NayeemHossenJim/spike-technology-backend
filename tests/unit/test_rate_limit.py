from __future__ import annotations

import pytest
from fastapi import HTTPException, Request

from app.services.rate_limit import enforce_auth_rate_limit
from tests.unit.test_security import make_settings


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expirations: list[tuple[str, int]] = []

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.expirations.append((key, seconds))
        return True


@pytest.mark.asyncio
async def test_auth_rate_limit_rejects_requests_above_window_limit() -> None:
    settings = make_settings()
    settings.auth_rate_limit_requests = 2
    settings.auth_rate_limit_window_seconds = 60
    redis = FakeRedis()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [],
            "client": ("203.0.113.10", 12345),
        }
    )

    await enforce_auth_rate_limit(request=request, redis=redis, settings=settings)  # type: ignore[arg-type]
    await enforce_auth_rate_limit(request=request, redis=redis, settings=settings)  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc_info:
        await enforce_auth_rate_limit(  # type: ignore[arg-type]
            request=request,
            redis=redis,
            settings=settings,
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "60"}
    assert len(redis.expirations) == 1
