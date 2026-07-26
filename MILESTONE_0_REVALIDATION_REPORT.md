# Milestone 0 — Phase 1 Revalidation Report

**Project:** Spike Technology / Liza AI analytics SaaS backend  
**Revalidation date:** 2026-07-26  
**Baseline archive:** `spike_backend_phase1.zip`  
**Baseline SHA-256:** `33f51f1fe9aa2af4b7499a49bfb94ee5289013a241c420a33ad55721209e9b12`

## Result

**Conditional pass.**

The source, lockfile, linting, packaging, migration chain, authentication behavior,
PostgreSQL/Redis integration suite, Celery task delivery, and dependency audit all passed
locally. Two defects found during revalidation were corrected and covered by tests.

The only remaining sign-off gate is a run with the repository's exact Docker Compose
images: PostgreSQL 16, Redis 7, the API container, and the worker container. Docker is not
installed in the revalidation workspace, so Milestone 0 must not be marked fully complete
until the Docker-native commands in this report pass.

## Validation evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Restored source integrity | Pass | Original archive opened cleanly; baseline SHA-256 recorded above |
| Frozen dependency resolution | Pass | `uv lock --check` |
| Python runtime | Pass | Python 3.12.13 |
| Static analysis | Pass | Ruff: all checks passed |
| Unit tests | Pass | 36 passed |
| Integration tests | Pass with environment caveat | 12 passed against a clean PostgreSQL-compatible database and real Redis |
| Full suite | Pass | 48 passed in 5.33 seconds |
| Alembic graph | Pass | One head: `0003_phase1_hardening` |
| Empty-database migration chain | Pass with environment caveat | `0001` → `0002` → `0003` applied during integration setup |
| Database test safety | Pass | Test URL must end in `_test`; migration-head guard is active |
| Celery/Redis delivery | Pass | Real worker returned `{"status": "ok", ...}` through Redis |
| OpenAPI creation | Pass | Application schema generated with all Phase 1 auth and `/users/me` routes |
| Python package build | Pass | Source distribution and wheel built successfully |
| Dependency vulnerability audit | Pass | No known vulnerabilities after lockfile update |
| Compose definition review | Pass | `api`, `worker`, `postgres`, and `redis`; PostgreSQL 16 and Redis 7 configured |
| Exact Docker Compose runtime | Pending | Docker unavailable in this workspace |

The local integration substitute was PGlite's PostgreSQL-compatible wire server plus
Redis 6.2.14. It exercised the real asyncpg, SQLAlchemy, Alembic, Redis, and application
paths, but it is not a substitute for the final PostgreSQL 16 / Redis 7 container gate.

## Phase 1 behavior revalidated

- Registration with normalized email, password hashing, Terms version/time, and approved
  Industry and Role fields.
- Six-digit signup-verification OTP.
- Six-digit forgot/reset-password OTP.
- Ten-minute OTP expiry.
- Lockout after five incorrect OTP attempts.
- Sixty-second resend cooldown and previous-code invalidation.
- Serialization of simultaneous resend, verify, and refresh operations.
- Login rejection for unverified or inactive users.
- JWT access tokens and rotating server-tracked refresh tokens.
- Refresh token stored only in an `HttpOnly`, `SameSite=Lax` cookie.
- Remember Me session-cookie and persistent-cookie behavior.
- Logout and password-reset refresh-token revocation.
- Auth rate limiting through Redis.
- `/api/v1/users/me` authorization.
- Development email remains `EMAIL_BACKEND=console`; AWS is not required for Phase 1.

## Defects corrected

### 1. Concurrent duplicate registration could return HTTP 500

The unique-email constraint can be raised by `flush()` when two registrations for the
same normalized email arrive together. The flush was outside the existing
`IntegrityError` handler, so this race could escape as an internal error.

The transaction boundary now translates both flush-time and commit-time unique
violations into the existing duplicate-email response, rolls back the failed
transaction, and does not send an OTP. A regression test was added.

### 2. Vulnerable development test dependency

The original lockfile used pytest 8.4.2, which was affected by the advisory tracked as
`PYSEC-2026-1845` / `CVE-2025-71176`. The development test stack was upgraded and locked
to pytest 9.1.1 and pytest-asyncio 1.4.0. All 48 tests passed after the upgrade, and the
follow-up dependency audit found no known vulnerabilities.

## Source changes

- `app/services/auth.py` — handle duplicate-email constraint failures at flush or commit.
- `tests/unit/test_auth_service.py` — regression coverage for the registration race.
- `pyproject.toml` — require fixed pytest and compatible pytest-asyncio versions.
- `uv.lock` — lock the updated development test stack.
- `README.md` — update the verified test count to 48: 36 unit plus 12 integration.
- `MILESTONE_0_REVALIDATION_REPORT.md` — this report.

No Phase 2 or Phase 3 functionality was added.

## Final Docker-native gate

Run this from a fresh extraction on a machine with Docker Desktop and `uv`. Use the
isolated Compose project name exactly as shown so the validation receives new volumes.

1. Create `.env` from `.env.example`, replace `JWT_SECRET_KEY` with a newly generated
   development secret, and keep:

   ```dotenv
   EMAIL_BACKEND=console
   AWS_REGION=
   SES_FROM_EMAIL=
   ```

2. Install the locked development environment and start the complete stack:

   ```bash
   uv sync --frozen --extra dev
   docker compose -p spike_m0 up -d --build
   docker compose -p spike_m0 ps
   ```

3. Confirm the one-head migration chain and run all checks.

   macOS/Linux:

   ```bash
   uv run alembic heads
   uv run alembic upgrade head
   uv run ruff check .
   RUN_INTEGRATION_TESTS=1 \
   TEST_DATABASE_URL=postgresql+asyncpg://spike:spike_local_password@localhost:5432/spike_test \
   uv run pytest -q
   ```

   Windows PowerShell:

   ```powershell
   uv run alembic heads
   uv run alembic upgrade head
   uv run ruff check .
   $env:RUN_INTEGRATION_TESTS="1"
   $env:TEST_DATABASE_URL="postgresql+asyncpg://spike:spike_local_password@localhost:5432/spike_test"
   uv run pytest -q
   ```

4. Verify API and worker health:

   ```bash
   curl http://localhost:8000/api/v1/health/live
   curl http://localhost:8000/api/v1/health/ready
   docker compose -p spike_m0 exec worker celery -A app.workers.celery_app:celery_app inspect ping
   docker compose -p spike_m0 exec api python -c "from app.workers.tasks import ping; print(ping.delay().get(timeout=10))"
   ```

## Final completion rule

Milestone 0 is complete only when the Docker-native run shows:

- PostgreSQL and Redis healthy.
- API and worker running.
- One Alembic head and successful migration from the new empty database.
- 48 tests passing.
- Both health endpoints returning `{"status":"ok"}`.
- Celery inspection responding and the queued task returning `{"status":"ok", ...}`.

After that evidence is recorded, development can move to Milestone 1 without carrying
an unverified Phase 1 foundation forward.
