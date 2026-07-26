# Spike Technology Backend

Production-oriented FastAPI backend for the confirmed Spike Technology v1 scope.

## Delivery status

| Milestone | Scope | Status |
| --- | --- | --- |
| **0 — Phase 1 revalidation** | Authentication, OTPs, secure refresh sessions, PostgreSQL/Redis/Celery, migrations, and tests | Complete |
| **1 — Business and subscription foundation** | Business onboarding, owner assignment, plan/subscription records, entitlement checks, and tenant isolation | Complete in this release |
| **2 — Stripe billing** | Checkout, signed/idempotent webhooks, paid subscription lifecycle, and billing history | Next |
| **3+ — Product workflow** | S3 uploads, file processing, Gemini, atomic credit ledger, dashboards, PDF, and admin operations | Pending |

This release deliberately does **not** expose uploads, Gemini calls, Stripe checkout,
dashboard creation, or PDF export.

## Current architecture

```text
FastAPI API ── asyncpg/SQLModel ── PostgreSQL
     │
     ├── Redis ── Celery worker (smoke task now; jobs in Phase 2)
     └── AWS SES via Boto3 + Asyncer (console sender in local development)

Authenticated user ── active RoleAssignment ── one Business
                                           └── current Subscription
                                                └── fail-closed Entitlements
```

The stack is fixed as follows: FastAPI, SQLModel, asyncpg, PostgreSQL, Alembic, Asyncer, Celery + Redis, Boto3, AWS SES, direct Gemini API via `google-genai` (Phase 2), pandas + `openpyxl` + `xlrd` (Phase 2), and WeasyPrint (Phase 3).

## Prerequisites

- Python **3.12**
- [uv](https://docs.astral.sh/uv/) package manager
- Docker Desktop (for local PostgreSQL and Redis)
- An AWS account, verified SES identity, and AWS credentials only when moving from local development to real email delivery

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

### 4. Install the locked development packages

```bash
uv sync --frozen --extra dev
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

`.env.example` uses `EMAIL_BACKEND=console`. Registration and password-reset **six-digit OTPs** are written to the API log, not sent to a real inbox. This makes local testing safe.

Both OTP flows use the approved policy: a 10-minute expiry, five maximum incorrect attempts, and a 60-second resend cooldown. A resend invalidates the earlier OTP. OTP replacement is serialized per user, so simultaneous resend requests cannot bypass the cooldown and create multiple active codes.

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
    "password": "CorrectHorseBattery9",
    "industry": "Technology",
    "job_role": "Engineer / Developer"
  }'
```

`industry` and `job_role` accept only the values shown in the Figma dropdowns. Registration records the configured `TERMS_VERSION` and the UTC acceptance time. The canonical field is `job_role`; `job_title` is accepted temporarily as a compatibility alias for Phase 1 clients.

With `EMAIL_BACKEND=console`, the API terminal prints a local-development message such as:

```text
Development signup-verification OTP for test@example.com: 012345. Expires in 10 minutes.
```

Copy the six digits exactly, including a leading zero if present. In production, AWS SES sends the OTP instead; it is never written to production application logs.

### Verify email

```bash
curl -X POST http://localhost:8000/api/v1/auth/verify-email \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","otp":"PASTE_SIX_DIGIT_OTP_HERE"}'
```

### Password reset with OTP

Request an OTP (the same endpoint is used by the UI's **Resend OTP** button after the 60-second cooldown):

```bash
curl -X POST http://localhost:8000/api/v1/auth/forgot-password \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com"}'
```

The local API log prints `Development password-reset OTP for test@example.com: 012345...`.
First verify the OTP, matching the verification-success screen in Figma:

```bash
curl -X POST http://localhost:8000/api/v1/auth/verify-password-reset-otp \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","otp":"PASTE_SIX_DIGIT_OTP_HERE"}'
```

Then set the new password with that verified OTP:

```bash
curl -X POST http://localhost:8000/api/v1/auth/reset-password \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","otp":"PASTE_SIX_DIGIT_OTP_HERE","new_password":"NewCorrectHorseBattery9"}'
```

### Login

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -c cookies.txt \
  -d '{"email":"test@example.com","password":"CorrectHorseBattery9","remember_me":true}'
```

The JSON response contains the short-lived access token. The rotating refresh token is deliberately **not** returned to browser JavaScript; it is stored in an `HttpOnly`, `SameSite=Lax` cookie scoped to `/api/v1/auth`.

- `remember_me: false` (the default) creates a browser-session cookie that is removed when the browser closes.
- `remember_me: true` creates a cookie lasting 30 days, matching `REFRESH_TOKEN_EXPIRE_DAYS`.
- Production cookies also use the `Secure` flag. Local development omits it so plain `http://localhost` works.

Save `access_token` from the response and call:

```bash
curl http://localhost:8000/api/v1/users/me \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE"
```

Rotate the refresh token and receive a new access token. There is no JSON request body:

```bash
curl -X POST http://localhost:8000/api/v1/auth/refresh \
  -b cookies.txt \
  -c cookies.txt
```

Log out, revoke the server-side refresh token, and delete the cookie:

```bash
curl -X POST http://localhost:8000/api/v1/auth/logout \
  -b cookies.txt \
  -c cookies.txt
```

Clients built against the earlier Phase 1 JSON refresh-token request must sign in again after this update so the server can set the secure cookie.

## Milestone 1 business flow

All tenant endpoints derive `business_id` from the authenticated user's active owner
assignment. A client cannot choose a tenant ID during onboarding.

### List public plans

```bash
curl http://localhost:8000/api/v1/plans
```

The migration seeds the three confirmed plans:

- Premium — `$59.99/month`
- Pro Plan — `$99.99/month`
- Enterprise — custom price

Every paid plan has the confirmed 15 full AI responses per billing period. Premium has
the confirmed 20-dashboard limit. Dashboard limits that were not approved for Pro,
Enterprise, or the standalone trial are intentionally absent and therefore fail closed.

### Onboard the authenticated user's business

```bash
curl -X POST http://localhost:8000/api/v1/businesses \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{"name":"Acme Analytics","industry":"Technology"}'
```

This one transaction creates:

- the user's single business;
- its active `owner` role assignment;
- a standalone 14-day trial subscription; and
- a 15-response trial entitlement.

Repeated or concurrent onboarding cannot create a second tenant.

### Read the current tenant state

```bash
curl http://localhost:8000/api/v1/businesses/me \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE"

curl http://localhost:8000/api/v1/subscriptions/me \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE"

curl http://localhost:8000/api/v1/entitlements/me \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE"
```

Cross-tenant subscription IDs return the same `404` response as unknown IDs. Tenant
foreign keys and unique indexes also enforce the boundary inside PostgreSQL.

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

The test harness always replaces `DATABASE_URL` with `TEST_DATABASE_URL`, refuses to start unless the parsed database name ends in `_test`, and applies the real Alembic chain before the tests. This prevents the destructive setup/cleanup from touching the development `spike` database.

This release contains one Alembic head, `0004_m1_foundation`, and 69 tests:
50 unit tests plus 19 PostgreSQL/Redis integration tests.

### If Alembic reports multiple heads

Run:

```bash
uv run alembic heads --verbose
```

This release must report only `0004_m1_foundation`. A second revision means an older or
locally generated migration file remained in `alembic/versions`, commonly after
extracting a release over an existing directory. Do **not** run `alembic upgrade heads`,
delete the file, or create a merge migration until the extra migration's contents and
`down_revision` have been reviewed. A safe alternative is to extract this release into a
new directory and copy only your `.env` file into it.

### Celery/Redis delivery smoke test

With the Compose stack running, first verify that the worker answers through Redis:

```bash
docker compose exec worker celery -A app.workers.celery_app:celery_app inspect ping
```

Then enqueue the Phase 1 smoke task from the API container and wait for its Redis-backed result:

```bash
docker compose exec api python -c "from app.workers.tasks import ping; print(ping.delay().get(timeout=10))"
```

The result must contain `{'status': 'ok', ...}`. The unit test uses eager execution only; these two commands verify the actual broker and worker connection.

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

## Milestone 1 acceptance checklist

- [ ] `docker compose ps` shows PostgreSQL and Redis healthy.
- [ ] `alembic upgrade head` succeeds.
- [ ] `/health/live` and `/health/ready` both return 200.
- [ ] A new user receives a six-digit signup-verification OTP (console locally; SES in production).
- [ ] Signup rejects values outside the Figma Industry and Your Role lists and records the Terms version/time.
- [ ] Unverified users cannot sign in.
- [ ] Verified users can login, refresh, logout, and access `/users/me`; the refresh token never appears in JSON.
- [ ] Unchecked Remember Me uses a session cookie; checked uses a persistent 30-day cookie.
- [ ] Password reset requires a verified six-digit OTP and revokes existing refresh tokens.
- [ ] `GET /plans` returns Premium, Pro Plan, and Enterprise with confirmed values only.
- [ ] Onboarding creates one business, one owner assignment, one 14-day trial, and one
      15-response trial entitlement.
- [ ] Repeated and simultaneous onboarding cannot create a second business.
- [ ] `/businesses/me`, `/subscriptions/me`, and `/entitlements/me` derive tenancy from
      the access token and active role assignment.
- [ ] A user requesting another tenant's subscription ID receives `404`.
- [ ] PostgreSQL rejects mismatched tenant foreign keys and a second live subscription.
- [ ] The worker responds to `inspect ping` and completes the queued `spike.system.ping` task.
- [ ] `ruff` and both test suites pass.
