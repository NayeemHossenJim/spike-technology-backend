from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.script import ScriptDirectory
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from alembic import command


def validate_test_database_url(database_url: str) -> str:
    """Refuse destructive test setup unless the database name is explicitly test-only."""

    database_name = make_url(database_url).database
    if not database_name or not database_name.lower().endswith("_test"):
        raise RuntimeError(
            "Integration tests require a database whose name ends with '_test'; "
            f"received {database_name!r}."
        )
    return database_url


# Must be configured before importing the application modules, which load Settings at import time.
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = validate_test_database_url(
    os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://spike:spike_local_password@localhost:5432/spike_test",
    )
)
test_redis_url = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")
os.environ["REDIS_URL"] = test_redis_url
os.environ["CELERY_BROKER_URL"] = test_redis_url
os.environ["CELERY_RESULT_BACKEND"] = os.getenv(
    "TEST_CELERY_RESULT_BACKEND",
    "redis://localhost:6379/14",
)
os.environ["JWT_SECRET_KEY"] = "test-only-secret-with-at-least-thirty-two-characters"
os.environ["EMAIL_BACKEND"] = "console"
os.environ["CORS_ORIGINS"] = "http://localhost:3000"
os.environ["TRUSTED_HOSTS"] = "localhost,127.0.0.1,testserver"

import app.models  # noqa: E402,F401
from app.api.deps import (  # noqa: E402
    enforce_ai_rate_limit_dependency,
    enforce_auth_rate_limit_dependency,
)
from app.db.session import get_session  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.email import get_email_sender  # noqa: E402
from app.services.gemini_gateway import (  # noqa: E402
    GeminiGatewayError,
    GeminiGeneration,
    get_gemini_gateway,
)
from app.services.processing_dispatch import (  # noqa: E402
    ReportProcessingDispatchError,
    get_report_processing_dispatcher,
)


@dataclass
class InMemoryEmailSender:
    verification_otps: list[tuple[str, str]] = field(default_factory=list)
    password_reset_otps: list[tuple[str, str]] = field(default_factory=list)

    async def send_verification_otp(self, recipient: str, otp: str) -> None:
        self.verification_otps.append((recipient, otp))

    async def send_password_reset_otp(self, recipient: str, otp: str) -> None:
        self.password_reset_otps.append((recipient, otp))


@dataclass
class InMemoryReportProcessingDispatcher:
    attempted_job_ids: list[UUID] = field(default_factory=list)
    dispatched_job_ids: list[UUID] = field(default_factory=list)
    failures_remaining: int = 0
    before_dispatch: Callable[[UUID], Awaitable[None]] | None = None

    async def dispatch(self, job_id: UUID) -> None:
        self.attempted_job_ids.append(job_id)
        if self.before_dispatch is not None:
            await self.before_dispatch(job_id)
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise ReportProcessingDispatchError
        self.dispatched_job_ids.append(job_id)


@dataclass
class InMemoryGeminiGateway:
    calls: list[tuple[str, str | None]] = field(default_factory=list)
    failure: GeminiGatewayError | None = None
    block: bool = False
    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def generate(
        self,
        *,
        prompt: str,
        system_instruction: str | None = None,
    ) -> GeminiGeneration:
        self.calls.append((prompt, system_instruction))
        self.started.set()
        if self.block:
            await self.release.wait()
        if self.failure is not None:
            raise self.failure
        sequence = len(self.calls)
        return GeminiGeneration(
            text=f"Deterministic AI response {sequence}.",
            provider_response_id=f"test-response-{sequence:04d}",
            model_version="gemini-test-model",
            finish_reason="STOP",
            prompt_token_count=20,
            response_token_count=8,
            thoughts_token_count=2,
            total_token_count=30,
        )

    async def aclose(self) -> None:
        return None


@pytest.fixture
def email_sender() -> InMemoryEmailSender:
    return InMemoryEmailSender()


@pytest.fixture
def processing_dispatcher() -> InMemoryReportProcessingDispatcher:
    return InMemoryReportProcessingDispatcher()


@pytest.fixture
def gemini_gateway() -> InMemoryGeminiGateway:
    return InMemoryGeminiGateway()


@pytest_asyncio.fixture
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip(
            "Set RUN_INTEGRATION_TESTS=1 with PostgreSQL and Redis running "
            "to execute integration tests."
        )
    project_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(project_root / "alembic"))
    migration_heads = ScriptDirectory.from_config(alembic_config).get_heads()
    if len(migration_heads) != 1:
        pytest.fail(
            "The Alembic migration graph must have exactly one head before test setup; "
            f"found: {', '.join(migration_heads)}. Resolve the migration branch before "
            "running integration tests. No test-database tables were dropped."
        )

    database_url = validate_test_database_url(os.environ["DATABASE_URL"])
    bootstrap_engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with bootstrap_engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.drop_all)
            await connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    finally:
        await bootstrap_engine.dispose()

    await asyncio.to_thread(command.upgrade, alembic_config, "head")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.drop_all)
            await connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(
    test_engine: AsyncEngine,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    yield async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
def app(
    session_factory: async_sessionmaker[AsyncSession],
    email_sender: InMemoryEmailSender,
    processing_dispatcher: InMemoryReportProcessingDispatcher,
    gemini_gateway: InMemoryGeminiGateway,
):
    test_app = create_app()

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    async def no_rate_limit() -> None:
        return None

    test_app.dependency_overrides[get_session] = override_session
    test_app.dependency_overrides[get_email_sender] = lambda: email_sender
    test_app.dependency_overrides[get_report_processing_dispatcher] = lambda: processing_dispatcher
    test_app.dependency_overrides[get_gemini_gateway] = lambda: gemini_gateway
    test_app.dependency_overrides[enforce_auth_rate_limit_dependency] = no_rate_limit
    test_app.dependency_overrides[enforce_ai_rate_limit_dependency] = no_rate_limit
    yield test_app
    test_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client
