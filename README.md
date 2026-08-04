# Spike Technology Backend

Production-oriented FastAPI backend for the confirmed Spike Technology v1 scope.

## Delivery status

| Milestone | Scope | Status |
| --- | --- | --- |
| **0 — Phase 1 revalidation** | Authentication, OTPs, secure refresh sessions, PostgreSQL/Redis/Celery, migrations, and tests | Complete |
| **1 — Business and subscription foundation** | Business onboarding, owner assignment, plan/subscription records, entitlement checks, and tenant isolation | Complete |
| **2 — Stripe billing** | Checkout, signed/idempotent webhooks, renewals, cancellation scheduling, billing history, and entitlement synchronization | Complete |
| **3 — Secure report uploads** | Private S3 storage, tenant-owned upload batches, presigned POSTs, file security validation, and upload status | Complete |
| **4 — Data processing** | Durable tenant-bound jobs, reliable Celery dispatch, bounded CSV/XLS/XLSX parsing, and private versioned artifacts | In progress — Stages 1–3 complete |
| **5+ — Product workflow** | Gemini, atomic credit ledger, dashboards, PDF, and admin operations | Pending |

The current Milestone 4 checkpoint creates durable report-processing jobs and writes
private normalized artifacts. It deliberately does **not** call Gemini, consume AI
credits, create dashboards, or export PDFs.

## Current architecture

```text
FastAPI API ── asyncpg/SQLModel ── PostgreSQL
     │
     ├── Redis ── Celery worker ── lease-guarded report processing
     ├── Stripe Checkout + signed webhooks ── local billing mirror
     ├── private versioned S3 ── exact-policy presigned report uploads
     └── AWS SES via Boto3 + Asyncer (console sender in local development)

Authenticated user ── active RoleAssignment ── one Business
                                           └── current Subscription
                                                └── fail-closed Entitlements
```

The stack is fixed as follows: FastAPI, SQLModel, asyncpg, PostgreSQL, Alembic,
Asyncer, Celery + Redis, Stripe, Boto3, AWS SES, `olefile`, `defusedxml`,
`openpyxl`, and `xlrd` for safe bounded workbook processing; direct Gemini API via
`google-genai`, pandas, and WeasyPrint arrive in later milestones.

## Prerequisites

- Python **3.12**
- [uv](https://docs.astral.sh/uv/) package manager
- Docker Desktop (for local PostgreSQL and Redis)
- A Stripe sandbox account and Stripe CLI for live Checkout/webhook testing
- An AWS account, verified SES identity, and AWS credentials only when moving from local development to real email delivery
- A dedicated private, versioned S3 bucket only when enabling report uploads

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

CSV/XLS/XLSX parser packages are part of the locked base runtime. The `phase2` extra
adds later Gemini/data-analysis packages; PDF support adds `--extra phase3`.

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

## Milestone 2 Stripe billing

Stripe is disabled by default, so Phase 1/Milestone 1 development still works without
Stripe credentials. Billing routes return `503` until all required sandbox values are
configured.

### Configure the Stripe sandbox

Create two monthly recurring Prices in Stripe:

- Premium — USD 59.99/month
- Pro Plan — USD 99.99/month

Enterprise remains sales-assisted and cannot be submitted to self-service Checkout.
Then set:

```dotenv
STRIPE_ENABLED=true
STRIPE_SECRET_KEY=sk_test_REPLACE_ME
STRIPE_WEBHOOK_SECRET=whsec_REPLACE_ME
STRIPE_PREMIUM_MONTHLY_PRICE_ID=price_REPLACE_ME
STRIPE_PRO_MONTHLY_PRICE_ID=price_REPLACE_ME
STRIPE_CHECKOUT_SUCCESS_URL=http://localhost:3000/billing/success?session_id={CHECKOUT_SESSION_ID}
STRIPE_CHECKOUT_CANCEL_URL=http://localhost:3000/billing/cancel
STRIPE_CHECKOUT_SESSION_MINUTES=60
STRIPE_WEBHOOK_TOLERANCE_SECONDS=300
```

The application refuses live Stripe secret keys outside production, test keys in
production, duplicate Premium/Pro Price IDs, malformed Price IDs, missing webhook
secrets, unsafe production callback URLs, and incomplete Stripe configuration. Before
creating Checkout, it also retrieves the configured Stripe Price and requires the exact
approved amount/currency, an active fixed monthly recurring Price, and licensed rather
than metered usage.

### Trial and Checkout behavior

Business onboarding remains the only action that starts the approved 14-day trial.
Checkout never starts a second trial. When enough time remains, the local trial end is
copied to Stripe as an absolute timestamp. Stripe requires that timestamp to be at least
48 hours away, so Checkout charges immediately when less than the safe 49-hour boundary
remains.

Create a Checkout Session with a unique client-generated idempotency key:

```bash
curl -X POST http://localhost:8000/api/v1/billing/checkout-sessions \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE" \
  -H "Idempotency-Key: PASTE_A_NEW_UUID_HERE" \
  -H "Content-Type: application/json" \
  -d '{"plan_code":"premium"}'
```

The client may submit only `plan_code`. Business ID, local subscription ID, Stripe
Customer, Stripe Price, trial dates, and entitlements are server-derived. Retrying with
the same key returns the same local Checkout operation. A second open Checkout is
rejected to prevent duplicate paid subscriptions. Pending operations receive a bounded
expiry before the Stripe request, so an interrupted network call cannot lock the tenant
out of Checkout indefinitely.

A Checkout redirect does not activate access. Only a verified Stripe subscription
snapshot received through the webhook endpoint can attach the paid Plan and synchronize
entitlements.

### Forward signed webhooks locally

```bash
stripe listen \
  --forward-to http://localhost:8000/api/v1/billing/webhooks/stripe
```

Copy the CLI's `whsec_...` value into `STRIPE_WEBHOOK_SECRET`, restart the API, and
subscribe the deployed endpoint to:

- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `checkout.session.expired`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `customer.subscription.paused`
- `customer.subscription.resumed`
- `invoice.created`
- `invoice.updated`
- `invoice.finalized`
- `invoice.paid`
- `invoice.payment_failed`
- `invoice.payment_action_required`
- `invoice.voided`
- `invoice.marked_uncollectible`

The webhook handler verifies the signature against the exact raw request bytes, enforces
test/live environment separation, stores no raw payment payload, deduplicates Stripe
Event IDs transactionally, tolerates out-of-order delivery, and rejects cross-tenant
metadata. A Stripe subscription with an unknown Price or status is fail-closed rather
than retaining its previous entitlements.

### Subscription lifecycle and billing history

Schedule cancellation at the end of the already-paid period:

```bash
curl -X POST http://localhost:8000/api/v1/billing/subscription/cancel \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE"
```

Undo a scheduled end-of-period cancellation before Stripe ends the subscription:

```bash
curl -X POST http://localhost:8000/api/v1/billing/subscription/resume \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE"
```

Immediate cancellation/refund behavior is intentionally absent because no refund or
proration policy has been approved. Renewal and payment-failure states arrive through
webhooks.

Read the tenant-scoped local invoice mirror:

```bash
curl "http://localhost:8000/api/v1/billing/history?limit=20&offset=0" \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE"
```

Only the owner can access Checkout, lifecycle, and billing-history routes. Hosted invoice
and PDF URLs are never returned across tenant boundaries.

## Milestone 3 secure report uploads

Report uploads are disabled by default. Authentication, onboarding, billing, and the
test suite continue to work without AWS credentials.

### Configure the private S3 bucket

Use a dedicated bucket and set:

```dotenv
AWS_REGION=us-east-1
S3_UPLOADS_ENABLED=true
S3_UPLOAD_BUCKET=your-private-report-upload-bucket
S3_UPLOAD_PREFIX=report-uploads
S3_PRESIGNED_POST_EXPIRE_MINUTES=10
```

Do not put AWS access keys in `.env`. Use an AWS profile locally and an IAM role in
deployment. The API refuses to issue or complete uploads unless the bucket has:

- all four S3 Block Public Access settings enabled;
- Bucket owner enforced object ownership;
- versioning enabled; and
- no public bucket policy.

Also configure default encryption and a bucket policy that denies non-TLS requests.
The application binds every upload to SSE-S3 (`AES256`) even when bucket-default
encryption is configured.

The application role needs these bucket inspection actions:

- `s3:GetBucketPublicAccessBlock`
- `s3:GetBucketOwnershipControls`
- `s3:GetBucketVersioning`
- `s3:GetBucketPolicyStatus`

Restrict these object actions to the configured upload prefix:

- `s3:PutObject`
- `s3:GetObject`
- `s3:GetObjectVersion`
- `s3:DeleteObjectVersion`

For browser uploads, configure S3 CORS with only the real frontend origins and the
`POST` method. Allow the headers required by the returned signed form. CORS does not
make the bucket public; IAM, the bucket policy, and the signed POST policy remain the
authorization boundary.

### Upload flow

Create one tenant-owned batch with one to five files:

```bash
curl -X POST http://localhost:8000/api/v1/report-uploads/batches \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "files": [
      {
        "filename": "sales.csv",
        "content_type": "text/csv",
        "size_bytes": 1254
      }
    ]
  }'
```

The request accepts only filename, canonical content type, and exact size. It never
accepts a business ID, user ID, S3 bucket, or object key. The response contains one
short-lived S3 `POST` URL and a `fields` object for each file. The frontend must submit
every returned field unchanged and add only the binary `file` form part.

After all S3 POSTs return `201`, ask the API to verify the batch:

```bash
curl -X POST \
  http://localhost:8000/api/v1/report-uploads/batches/PASTE_BATCH_ID/complete \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE"
```

Read status without exposing storage coordinates:

```bash
curl \
  http://localhost:8000/api/v1/report-uploads/batches/PASTE_BATCH_ID \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE"
```

The server verifies the current immutable S3 version, exact byte count, canonical
content type, encryption, cache policy, tenant/batch/upload metadata, ETag, and upload
time before reading that exact version. Rejected object versions are deleted.

Only UTF-8 CSV, legacy XLS, and XLSX files are accepted, with a 25 MB maximum per file.
Filename tricks, executable double extensions, binary files renamed as CSV, malformed
or encrypted workbooks, XLS macro/embedded content, XLSX path traversal, macros,
ActiveX, embedded objects, external links, corrupt ZIP members, and excessive ZIP
expansion are rejected. These checks are structural security validation, not general
antivirus scanning.

Each verified file now receives one durable job before broker publication. The worker
reads only the recorded immutable source version, applies fixed sheet/row/column/cell
and output limits, and writes deterministic gzip JSONL plus schema/profile JSON to
server-owned, private, versioned S3 keys. Malformed data fails with a safe code;
temporary storage failures retry. No public object or file-download endpoint exists.

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

This release contains one Alembic head, `0007_m3_upload_positions`, and 106 tests:
78 unit tests plus 28 PostgreSQL/Redis integration tests.

### If Alembic reports multiple heads

Run:

```bash
uv run alembic heads --verbose
```

This release must report only `0007_m3_upload_positions`. A second revision means an older or
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

## Milestone 3 acceptance checklist

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
- [ ] Stripe is disabled safely when its configuration is absent.
- [ ] Premium and Pro create sandbox Checkout Sessions using configured monthly Price IDs;
      Enterprise is rejected as sales-assisted.
- [ ] Checkout rejects a configured Stripe Price unless its active state, fixed amount,
      currency, monthly interval, and licensed usage match the approved local Plan.
- [ ] Checkout preserves only the unused portion of the onboarding trial and never starts
      another 14 days.
- [ ] Same-key Checkout retries are idempotent and concurrent/different open Checkouts
      cannot create two paid subscriptions.
- [ ] Invalid or wrong-environment webhooks make no billing changes.
- [ ] Duplicate and concurrent Stripe Event deliveries are processed once.
- [ ] Subscription renewals update item-level period boundaries and paid Plan entitlements.
- [ ] Invoice events populate tenant-scoped billing history without stale status regression.
- [ ] End-of-period cancellation and resumption synchronize with Stripe.
- [ ] An unknown Stripe Price/status removes effective paid entitlements fail-closed.
- [ ] Report-upload routes return `503` while S3 uploads are intentionally disabled.
- [ ] The enabled upload bucket passes Block Public Access, ownership, versioning, and
      non-public-policy checks.
- [ ] A batch accepts one to five CSV/XLS/XLSX files and rejects files over 25 MB.
- [ ] Presigned POST fields bind the server-owned key, exact size, MIME type, SSE,
      cache policy, and tenant/upload metadata.
- [ ] The browser can upload using only the returned URL and fields.
- [ ] Completion accepts only the verified immutable S3 version and records its status.
- [ ] Suspicious, malformed, active-content, mislabeled, or expired uploads are rejected
      and their inspected object versions are deleted.
- [ ] Another tenant receives the same `404` as an unknown batch ID.
- [ ] PostgreSQL rejects a creator/uploader from another tenant and inconsistent upload
      status fields.
- [ ] Each verified upload receives one tenant-bound processing job before dispatch;
      partial batches process only their valid files.
- [ ] CSV, XLS, and XLSX parsing observes fixed sheet, row, column, cell, expansion, and
      normalized-output limits without evaluating workbook formulas.
- [ ] Completed jobs reference immutable gzip JSONL and profile JSON versions under
      server-owned tenant/job keys; duplicate delivery does not rewrite completed jobs.
- [ ] Temporary storage failures retry the same job UUID, while malformed inputs fail
      with safe detail-free error codes.
- [ ] The worker responds to `inspect ping` and completes the queued `spike.system.ping` task.
- [ ] `ruff` and both test suites pass.
