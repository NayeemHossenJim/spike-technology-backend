from __future__ import annotations

from collections.abc import AsyncGenerator

from httpx import ASGITransport, AsyncClient
from redis.exceptions import RedisError

from app.db.session import get_session
from app.main import create_app
from app.services.redis import get_redis


class HealthySession:
    async def execute(self, _statement) -> None:
        return None


class HealthyRedis:
    async def ping(self) -> bool:
        return True


class UnavailableRedis:
    async def ping(self) -> bool:
        raise RedisError("unavailable")


async def override_session() -> AsyncGenerator[HealthySession, None]:
    yield HealthySession()


async def test_liveness_endpoint_and_security_headers() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


async def test_readiness_checks_postgres_and_redis() -> None:
    app = create_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_redis] = lambda: HealthyRedis()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_returns_503_when_dependency_is_unavailable() -> None:
    app = create_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_redis] = lambda: UnavailableRedis()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
