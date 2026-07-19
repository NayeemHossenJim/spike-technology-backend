from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

# Must be configured before importing the application modules, which load Settings at import time.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://spike:spike_local_password@localhost:5432/spike_test",
    ),
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/15")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/14")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-with-at-least-thirty-two-characters")
os.environ.setdefault("EMAIL_BACKEND", "console")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("TRUSTED_HOSTS", "localhost,127.0.0.1,testserver")

import app.models  # noqa: E402,F401
from app.api.deps import enforce_auth_rate_limit_dependency  # noqa: E402
from app.db.session import get_session  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.email import get_email_sender  # noqa: E402


@dataclass
class InMemoryEmailSender:
    verification_otps: list[tuple[str, str]] = field(default_factory=list)
    password_reset_otps: list[tuple[str, str]] = field(default_factory=list)

    async def send_verification_otp(self, recipient: str, otp: str) -> None:
        self.verification_otps.append((recipient, otp))

    async def send_password_reset_otp(self, recipient: str, otp: str) -> None:
        self.password_reset_otps.append((recipient, otp))


@pytest.fixture
def email_sender() -> InMemoryEmailSender:
    return InMemoryEmailSender()


@pytest_asyncio.fixture
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip(
            "Set RUN_INTEGRATION_TESTS=1 with PostgreSQL and Redis running "
            "to execute integration tests."
        )
    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.drop_all)
        await connection.run_sync(SQLModel.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(
    test_engine: AsyncEngine,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    yield async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
def app(session_factory: async_sessionmaker[AsyncSession], email_sender: InMemoryEmailSender):
    test_app = create_app()

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    async def no_rate_limit() -> None:
        return None

    test_app.dependency_overrides[get_session] = override_session
    test_app.dependency_overrides[get_email_sender] = lambda: email_sender
    test_app.dependency_overrides[enforce_auth_rate_limit_dependency] = no_rate_limit
    yield test_app
    test_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client
