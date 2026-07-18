# Spike Technology Backend

Production-oriented FastAPI backend for the confirmed Spike Technology v1 scope.

## Delivery plan

| Phase | Scope | Completion result |
| --- | --- | --- |
| **1 — Foundation (this codebase)** | FastAPI structure, PostgreSQL/asyncpg, Alembic, Celery/Redis wiring, JWT auth, email verification, password reset, SES adapter, RBAC primitives, security middleware, health checks, tests | A secure user can register, verify email, sign in, refresh/logout, reset password, and call `/users/me`. |
| **2 — Financial-data and AI workflow** | S3 presigned POST uploads, file validation (five files; 25 MB per file), pandas/Excel ingestion, header mapping, business onboarding, Celery job state, Gemini runs, 15-credit ledger, three dashboard types | Users can upload supported data, map it, generate an authorized insight/dashboard, and see job progress. |
| **3 — Commercial and operations** | Stripe checkout/trial/webhooks, plan and quota enforcement, billing history, customer-service/account-only access, super-admin operations, PDF export through WeasyPrint, audit/activity records | The paid product, admin workflows, billing, and PDFs are complete. |

Phase 1 deliberately does **not** expose uploads, AI, Stripe, or dashboards. Those endpoints are Phase 2/3 work, so no unapproved behavior is invented.

## Phase 1 architecture

```text
FastAPI API ── asyncpg/SQLModel ── PostgreSQL
     │
     ├── Redis ── Celery worker (smoke task now; jobs in Phase 2)
     └── AWS SES via Boto3 + Asyncer (console sender in local development)
```

The stack is fixed as follows: FastAPI, SQLModel, asyncpg, PostgreSQL, Alembic, Asyncer, Celery + Redis, Boto3, AWS SES, direct Gemini API via `google-genai` (Phase 2), pandas + `openpyxl` + `xlrd` (Phase 2), and WeasyPrint (Phase 3).

## Prerequisites

- Python **3.12**
- [uv](https://docs.astral.sh/uv/) package manager
- Docker Desktop (for local PostgreSQL and Redis)
- An AWS account, verified SES identity, and AWS credentials only when moving from local development to real email delivery

Docker is not available in the current coding workspace, so the integration-test commands below are included and ready to run on your computer, but must be run where Docker Desktop is installed.

## First-time installation

### 1. Enter the project

```bash
cd spike_backend
```

### 2. Create the environment file

macOS/Linux:

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

### 3. Create a development JWT secret

```bash
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy the generated value into `JWT_SECRET_KEY` in `.env`. Never commit `.env`.

### 4. Install Phase 1 packages

```bash
uv sync --extra dev
```

Phase 2 packages will be installed later with `uv sync --extra dev --extra phase2`. Phase 3 adds `--extra phase3`.

### 5. Start PostgreSQL and Redis

```bash
docker compose up -d postgres redis
```

Check that both containers are healthy:

```bash
docker compose ps
```

The first database initialization creates two databases:

- `spike` — local development
- `spike_test` — integration tests only

### 6. Apply the schema migration

```bash
uv run alembic upgrade head
```

### 7. Run the API

```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/docs` to inspect the API.

### 8. Run the Celery worker in another terminal

```bash
cd spike_backend
uv run celery -A app.workers.celery_app:celery_app worker --loglevel=INFO
```

Alternatively, build and run all services in containers after configuring `.env`:

```bash
docker compose up --build
```

## Local development email

`.env.example` uses `EMAIL_BACKEND=console`. Registration and password-reset links are written to the API log, not sent to a real inbox. This makes local testing safe.

For AWS SES, set the following only after you have verified your sending identity in SES:

```dotenv
EMAIL_BACKEND=ses
AWS_REGION=your-aws-region
SES_FROM_EMAIL=no-reply@your-domain.com
```

Set `APP_ENV=production` only in deployed environments. Production validation refuses the console sender, weak JWT secrets, wildcard CORS, or missing SES settings.

## API smoke-test flow

### Health

```bash
curl http://localhost:8000/api/v1/health/live
curl http://localhost:8000/api/v1/health/ready
```

Both must return:

```json
{"status":"ok"}
```

### Register

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "full_name": "Test User",
    "email": "test@example.com",
    "password": "CorrectHorseBattery9"
  }'
```

With `EMAIL_BACKEND=console`, the API terminal prints a local-development message such as
`Verification URL: http://localhost:3000/verify-email?token=...`. Copy the opaque value
after `token=`. This URL is deliberately logged only by the console sender; production uses
AWS SES and does not write verification tokens to application logs.

### Verify email

```bash
curl -X POST http://localhost:8000/api/v1/auth/verify-email \
  -H "Content-Type: application/json" \
  -d '{"token":"PASTE_THE_TOKEN_HERE"}'
```

### Login

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"CorrectHorseBattery9"}'
```

Save `access_token` and call:

```bash
curl http://localhost:8000/api/v1/users/me \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE"
```

Use `/auth/refresh` with the returned `refresh_token` to rotate tokens, and `/auth/logout` to revoke the refresh token.

## Testing

### Fast checks, no Docker required

```bash
uv run ruff check .
uv run pytest tests/unit
```

### Full integration tests

Start PostgreSQL and Redis first with `docker compose up -d postgres redis`.

macOS/Linux:

```bash
RUN_INTEGRATION_TESTS=1 \
TEST_DATABASE_URL=postgresql+asyncpg://spike:spike_local_password@localhost:5432/spike_test \
uv run pytest tests/integration -q
```

Windows PowerShell:

```powershell
$env:RUN_INTEGRATION_TESTS="1"
$env:TEST_DATABASE_URL="postgresql+asyncpg://spike:spike_local_password@localhost:5432/spike_test"
uv run pytest tests/integration -q
```

The integration suite recreates tables only in `spike_test`; it never uses the development `spike` database.

## Operational commands

```bash
# Check the migration revision
uv run alembic current

# Create a new migration after a model change
uv run alembic revision --autogenerate -m "describe_change"

# Create the first super-admin after migrations
uv run python -m app.scripts.create_super_admin \
  --email admin@your-domain.com \
  --full-name "Platform Admin" \
  --password "Use-A-Real-Long-Secret9"
```

Never pass real production passwords through shell history. In production, use a one-time secure operational process or a secret manager.

## Phase 1 acceptance checklist

- [ ] `docker compose ps` shows PostgreSQL and Redis healthy.
- [ ] `alembic upgrade head` succeeds.
- [ ] `/health/live` and `/health/ready` both return 200.
- [ ] A new user receives a verification flow (console locally; SES in production).
- [ ] Unverified users cannot sign in.
- [ ] Verified users can login, refresh, logout, and access `/users/me`.
- [ ] Password reset revokes existing refresh tokens.
- [ ] `ruff` and both test suites pass.
