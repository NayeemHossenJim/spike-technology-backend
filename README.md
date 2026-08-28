# Spike Technology Backend - Master Documentation

Welcome to the unified master documentation for the **Spike Technology / Liza AI Analytics SaaS Backend**.

## Table of Contents
- [1. Project Status, Overview & Quickstart](#1-project-status-overview--quickstart)
- [2. Technical Handover & Continuation Guide](#2-technical-handover--continuation-guide)
- [3. Master Implementation Plan](#3-master-implementation-plan)
- [4. V1 Client Handover Summary](#4-v1-client-handover-summary)
- [5. Operational Runbook & Observability](#5-operational-runbook--observability)
- [6. Staging Deployment & Recovery Runbook](#6-staging-deployment--recovery-runbook)
- [7. Performance & Reliability Baselines](#7-performance--reliability-baselines)
- [8. Milestone Specifications & Contracts](#8-milestone-specifications--contracts)
  - [Milestone 5 Contract: Gemini AI & Credit Ledger](#milestone-5-contract---gemini-ai-and-atomic-credit-ledger)
  - [Milestone 7 Contract: Admin Operations](#milestone-7-contract---admin-operations)
- [9. Historical Milestone Implementation Reports](#9-historical-milestone-implementation-reports)
  - [Milestone 0 Revalidation Report](#milestone-0--phase-1-revalidation-report)
  - [Milestone 1 Implementation Report](#milestone-1--business-and-subscription-foundation)
  - [Milestone 2 Implementation Report](#milestone-2--stripe-billing)
  - [Milestone 3 Implementation Report](#milestone-3--secure-report-uploads)

---

<a id="1-project-status-overview--quickstart"></a>
# 1. Project Status, Overview & Quickstart

# Spike Technology Backend

<!-- SPIKE_PROJECT_STATUS_START -->

## Project status

**Last updated:** 2026-08-11

Backend implementation through **Milestone 7 - Admin Operations** is complete,
validated, merged into `develop`, and pushed to the remote repository.

| Checkpoint | Status |
|---|---|
| Current branch | `develop` |
| Milestone 7 merge | `2c5ee900ba987393f4aaa4986b780798962079be` |
| Alembic head | `0020_gap06_v1_entitlements` |
| Full project regression | **478 passed** |
| Integration regression | **98 passed** |
| Milestone 7 integration | **22 passed** |
| PostgreSQL | Healthy |
| Redis | Healthy |
| FastAPI API | Running |
| Celery worker | Running |

### Completed milestones

- [x] Phase 1 / Milestone 0 - Authentication and baseline hardening
- [x] Milestone 1 - Business, tenancy, plans and entitlements
- [x] Milestone 2 - Stripe billing
- [x] Milestone 3 - Secure report uploads
- [x] Milestone 4 - Report data processing
- [x] Milestone 5 - Gemini AI, atomic credits and conversations
- [x] Milestone 6 - Dashboards, snapshots, exports and PDF
- [x] Milestone 7 - Admin operations and security controls
- [x] V1 Figma-Contract Gaps - Paginated upload history, profile fields, account security, and dashboard integrations

### Next milestone 

**Milestone 8 - Production Readiness & Deployment**

The next phase focuses on production infrastructure rather than adding
unplanned backend features:

- production environment and secrets;
- private S3 storage;
- production email delivery;
- Stripe production readiness;
- production PostgreSQL and Redis;
- backups and disaster recovery;
- CI/CD;
- observability and alerting;
- security and performance validation;
- frontend/backend staging E2E testing;
- staging/UAT;
- production deployment and rollback readiness.

For the full completed-work record and next-step execution plan, see
[`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md).

<!-- SPIKE_PROJECT_STATUS_END -->

Production-oriented FastAPI backend for the confirmed Spike Technology v1 scope.

## Delivery status

| Milestone | Scope | Status |
| --- | --- | --- |
| **0 — Phase 1 revalidation** | Authentication, OTPs, secure refresh sessions, PostgreSQL/Redis/Celery, migrations, and tests | Complete |
| **1 — Business and subscription foundation** | Business onboarding, owner assignment, plan/subscription records, entitlement checks, and tenant isolation | Complete |
| **2 — Stripe billing** | Checkout, signed/idempotent webhooks, renewals, cancellation scheduling, billing history, and entitlement synchronization | Complete |
| **3 — Secure report uploads** | Private S3 storage, tenant-owned upload batches, presigned POSTs, file security validation, and upload status | Complete |
| **4 — Data processing** | Durable tenant-bound jobs, reliable Celery dispatch, bounded CSV/XLS/XLSX parsing, and private versioned artifacts | Complete |
| **5+ — Product workflow** | Gemini, atomic credit ledger, dashboards, PDF, and admin operations | Complete |

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

`.env.example` uses `EMAIL_BACKEND=console`. Registration, password-reset, and
new-email verification **six-digit OTPs** are written to the API log, not sent to a
real inbox. This makes local testing safe.

All OTP flows use the approved policy: a 10-minute expiry, five maximum incorrect
attempts, and a 60-second resend cooldown. A resend invalidates the earlier OTP. OTP
replacement is serialized per user, so simultaneous resend requests cannot bypass the
cooldown and create multiple active codes.

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
  -d '{"email":"test@example.com","otp":"PASTE_SIX_DIGIT_OTP_HERE","new_password":"<NEW_PASSWORD>"}'
```

### Login

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -c cookies.txt \
  -d '{"email":"test@example.com","password":"CorrectHorseBattery9","remember_me":true}'
```

When email two-step verification is disabled, the JSON response contains the short-lived
access token. The rotating refresh token is deliberately **not** returned to browser
JavaScript; it is stored in an `HttpOnly`, `SameSite=Lax` cookie scoped to
`/api/v1/auth`. Accounts that enable the optional email-code step instead receive the
documented `202` challenge response described below; no session exists until that code
is verified.

- `remember_me: false` (the default) creates a browser-session cookie that is removed when the browser closes.
- `remember_me: true` creates a cookie lasting 30 days, matching `REFRESH_TOKEN_EXPIRE_DAYS`.
- Production cookies also use the `Secure` flag. Local development omits it so plain `http://localhost` works.

Save `access_token` from the response and call:

```bash
curl http://localhost:8000/api/v1/users/me \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE"
```

### Edit the authenticated profile

`GET /api/v1/users/me` returns the persisted text-field contract for Figma's Edit
Profile screen. Update any subset of the non-sensitive profile fields with
`PATCH /api/v1/users/me`:

```bash
curl -X PATCH http://localhost:8000/api/v1/users/me \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "full_name": "Brendoly Cooper",
    "username": "B. Cooper",
    "phone_number": "(207) 555-0119",
    "industry": "Accounting",
    "job_role": "Accountant",
    "street_address": "2972 Westheimer Rd.",
    "city": "Santa Ana",
    "state": "Illinois",
    "postal_code": "85486",
    "bio": "Finance operations lead."
  }'
```

All text is trimmed, the bio is limited to the Figma-confirmed 200 characters, and
unknown fields are rejected. Optional fields can be cleared explicitly with JSON
`null`; omitted fields are preserved. If the user already owns a business, updating
or clearing `industry` updates both profile and business in one transaction so saved
dashboard cards and PDF exports cannot show stale industry data.

Email, password, role, account status, verification status, and session generation
are deliberately excluded from this endpoint. Those values require separate verified
security or administrative workflows and cannot be changed through Edit Profile.

### Change email, password, or delete the account

Figma's **Change email & password** settings surface maps to dedicated authenticated
security routes; these fields never pass through the general profile patch. Request a
new-email challenge with the current password:

```bash
curl -X POST http://localhost:8000/api/v1/users/me/email-change \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "old_email":"test@example.com",
    "new_email":"replacement@example.com",
    "current_password":"CorrectHorseBattery9"
  }'
```

The old address must equal the authenticated account, the new address must be unused,
and the password must reauthenticate the user. The new address is unchanged until its
six-digit OTP is confirmed:

```bash
curl -X POST http://localhost:8000/api/v1/users/me/email-change/confirm \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{"new_email":"replacement@example.com","otp":"PASTE_SIX_DIGIT_OTP_HERE"}'
```

Change the password using Figma's Old Password, Create Password, and Confirm Password
fields:

```bash
curl -X PUT http://localhost:8000/api/v1/users/me/password \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "current_password":"CorrectHorseBattery9",
    "new_password":"DifferentHorseBattery8",
    "confirm_password":"DifferentHorseBattery8"
  }'
```

Both completed credential changes increment the account session generation, revoke all
refresh tokens, invalidate pending authentication OTPs, and clear the browser refresh
cookie. Every previously issued access token also becomes invalid immediately, so the
client must discard it and return to sign-in. Password reset now applies the same access
token invalidation rule.

Account deletion requires a confirmation dialog that collects the current password and
the exact word `DELETE`; this confirmation is intentionally stronger than Figma's bare
Delete Account link:

```bash
curl -X DELETE http://localhost:8000/api/v1/users/me \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{"current_password":"CorrectHorseBattery9","confirmation":"DELETE"}'
```

Deletion immediately disables the account, revokes every session and OTP, releases the
old email for a new registration, removes profile identity fields, deactivates the owner
assignment, and ends a local standalone trial. Tenant, report, billing, invoice, and
audit records are retained under a non-login anonymized user identity for referential
and financial integrity. Deletion fails closed while a Checkout Session is pending/open
or a Stripe-managed subscription is still current; the user must finish/cancel Checkout
and allow the subscription to end first. The final active super administrator is also
protected from self-deletion.

Frontend integration must add two security confirmations not present in the supplied
frame: a current-password field before requesting an email change, and an OTP step for
the new address. The Delete Account control must open the password plus `DELETE`
confirmation dialog and render the documented `409` billing/last-admin messages.

### Email two-step verification

Figma's **Security Settings** flow sends a six-digit code to the registered email before
each login. The backend names this feature *email two-step verification*, not strong MFA:
[NIST SP 800-63B](https://pages.nist.gov/800-63-4/sp800-63b/authenticators/)
explicitly excludes email from acceptable out-of-band authenticators, and the
[OWASP email guidance](https://cheatsheetseries.owasp.org/cheatsheets/Email_Validation_and_Verification_Cheat_Sheet.html)
describes email as a weak factor. A future TOTP or WebAuthn factor should replace this
channel when standards-compliant MFA is required.

Read the authenticated state with `GET /api/v1/users/me/email-two-step`. Enabling the
flow requires both the registered email and the current password because OWASP advises
reauthentication before changing an authentication factor:

```bash
curl -X POST http://localhost:8000/api/v1/users/me/email-two-step/enable \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "email":"test@example.com",
    "current_password":"CorrectHorseBattery9"
  }'
```

The `202` response contains an opaque `challenge_id`, a masked destination, and the
remaining lifetime, but never the OTP. Confirm the emailed code:

```bash
curl -X POST http://localhost:8000/api/v1/users/me/email-two-step/enable/confirm \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "challenge_id":"PASTE_CHALLENGE_UUID_HERE",
    "otp":"PASTE_SIX_DIGIT_OTP_HERE"
  }'
```

Enabling or disabling the step increments the account session generation, revokes all
refresh tokens, invalidates every pending email-code challenge, and clears the browser
cookie. A separate security notice is sent after either setting change. The client must
discard its old access token and return to sign-in. This current-password requirement is
an intentional frontend addition to the supplied Figma modal, which currently shows
only the email field.

After the password succeeds for an enabled account, `POST /api/v1/auth/login` returns
`202` with `verification_required: true`; it returns no access token and sets no refresh
cookie. Complete sign-in with:

```bash
curl -X POST http://localhost:8000/api/v1/auth/email-two-step/verify \
  -H "Content-Type: application/json" \
  -c cookies.txt \
  -d '{
    "challenge_id":"PASTE_CHALLENGE_UUID_HERE",
    "otp":"PASTE_SIX_DIGIT_OTP_HERE"
  }'
```

Only this successful verification creates the access/refresh pair. `remember_me` is
bound server-side to the challenge and cannot be changed during verification. Login
resends use `POST /api/v1/auth/email-two-step/resend`; authenticated enable/disable
resends use `POST /api/v1/users/me/email-two-step/resend`. Requests inside
`OTP_RESEND_COOLDOWN_SECONDS` reuse the live challenge without sending another email.
Every challenge is single-use, bound to the account session generation, expires within
`EMAIL_TWO_STEP_EXPIRE_MINUTES` (maximum 10), and is exhausted after
`OTP_MAX_ATTEMPTS` failures.

Disabling is also a reauthenticated two-step operation: request it with
`POST /api/v1/users/me/email-two-step/disable` and the current password, then submit the
returned challenge and emailed OTP to
`POST /api/v1/users/me/email-two-step/disable/confirm`.

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

Both the completion response and this read response include a `processing_jobs` list
ordered with the batch files. Each item exposes only its job ID, report-upload ID,
status, timestamps, safe error code, and completed row/sheet counts. Pending or rejected
files have no job. Poll this batch resource until the required jobs are `completed`, then
pass those job IDs as `source_processing_job_ids` when creating a dashboard snapshot.

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

## Report-grounded dashboard insight flow

The Generate Insight backend follows the three-step product flow without guessing report
headers. Owners first read the versioned dashboard contract and the safe header/profile
of each completed processing job:

```bash
curl http://localhost:8000/api/v1/dashboards/contracts \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE"

curl \
  http://localhost:8000/api/v1/dashboards/sources/PASTE_PROCESSING_JOB_ID/schema \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE"
```

The contract exposes only the approved Executive Summary, Financial Performance, and
Operational KPI types, their mapping fields, recommended reports, and the nine approved
business goals. It also publishes the calculation field groups that must be mapped from
the same report sheet, allowing the client to reject an invalid setup before submission.
The source-schema response contains sheet names, normalized header names, null/non-null
counts, and value-type counts. It never returns report rows, S3 coordinates, or object
credentials.

Create the dashboard only after the user has explicitly mapped the chosen report headers.
For example, an Executive Summary request includes all seven required semantic fields:

```json
{
  "dashboard_type": "executive_summary",
  "title": "Executive Insight",
  "configuration": {"currency": "USD", "period": "monthly"},
  "insight_configuration": {
    "version": 1,
    "goals": ["improve_cash_flow", "reduce_expenses"],
    "datasets": [
      {
        "processing_job_id": "PASTE_PROCESSING_JOB_ID",
        "sheet_index": 0,
        "sheet_name": "data",
        "columns": {
          "period": "Date",
          "location": "Department",
          "expenses": "Expenses",
          "revenue": "Revenue",
          "net_cash": "Cash Balance",
          "category": "Category",
          "gross_margin": "Gross margin"
        }
      }
    ]
  }
}
```

Submit that JSON to `POST /api/v1/dashboards`, then materialize it with
`POST /api/v1/dashboards/{dashboard_id}/snapshots`. For a typed dashboard, the submitted
source-job set must exactly match the mapped job set. The service validates every sheet
and column against the immutable profile, reads only the pinned processed artifact
versions, and produces a deterministic snapshot containing type-specific KPIs,
chart-ready series, breakdowns/tables, anomalies, risks, evidence-backed goal
recommendations, and calculated-versus-AI explainability. The Executive cash-flow chart
separates mapped actuals from a clearly labelled three-period linear projection based on
up to the three latest mapped period changes. Invalid numeric mappings or changed/missing
artifacts fail closed instead of silently substituting another column. The same semantic
measure and period cannot come from multiple mapped sheets because the service cannot
safely infer whether those rows are duplicates or intentional partitions. Required
numeric measures must also cover the same mapped periods before the service calculates
cross-measure totals, ratios, margins, or forecasts.

Generate the snapshot-bound Gemini narrative with a new idempotency key:

```bash
curl -X POST \
  http://localhost:8000/api/v1/dashboards/PASTE_DASHBOARD_ID/snapshots/PASTE_SNAPSHOT_ID/analysis \
  -H "Authorization: Bearer PASTE_ACCESS_TOKEN_HERE" \
  -H "Idempotency-Key: PASTE_A_NEW_UUID_HERE"
```

The prompt contains bounded calculated evidence plus upload/job/dashboard/snapshot
provenance, never normalized report rows. A successful response must contain exactly
Summary, Opportunity, Recommendation, and Disclaimer sections; an unusable provider
response releases the reserved AI credit. Snapshot detail reads return the stored AI
analysis alongside the immutable calculated payload. Existing dashboards that omit
`insight_configuration` remain readable and materializable, but intentionally cannot use
the dedicated typed-analysis endpoint.

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
- [ ] Dashboard contract and source-schema routes expose only the approved three types,
      nine goals, and safe immutable report header metadata.
- [ ] Typed dashboard snapshots require exact explicit mappings and calculate the
      type-specific KPI, chart, table, anomaly, risk, and recommendation payloads.
- [ ] Dashboard AI analysis is idempotent, consumes one governed AI credit, uses only
      bounded snapshot evidence, and returns the required four narrative sections.
- [ ] The worker responds to `inspect ping` and completes the queued `spike.system.ping` task.
- [ ] `ruff` and both test suites pass.

---

<a id="2-technical-handover--continuation-guide"></a>
# 2. Technical Handover & Continuation Guide

# Spike Technology Backend
## Technical Handover and Continuation Guide

**Handover baseline:** `develop @ 38f2816`  
**Repository:** `https://github.com/NayeemHossenJim/spike-technology-backend`  
**Date:** 4 August 2026  
**Status:** Outgoing developer's assigned half completed

> **Security:** This file must not contain passwords, API keys, webhook secrets, database credentials, AWS keys, or production environment values. Transfer secrets through an approved secure channel.

---

## 1. Executive summary

The backend has reached the agreed midpoint. The foundation and Milestones 1–4 are complete and merged into `develop` through Pull Request #4.

Verified handover state:

- Branch: `develop`
- Commit: `38f2816`
- Tracking: `origin/develop`
- Working tree: clean
- Milestone 4 feature branch: deleted locally and remotely
- GitHub checks: passed before merge

The practical overall project estimate is approximately **52%**. Under the team's 50/50 ownership model, the outgoing developer's assigned half is complete. The incoming developer owns Milestones 5–9, final end-to-end integration, and production readiness.

## 2. Approved first-release constraints

| Area | Constraint |
|---|---|
| Authentication | Email/password; six-digit OTP for signup verification and password reset; Remember Me approved; no optional 2FA initially |
| Business tenancy | One business per account context; tenant isolation required |
| Trial and billing | 14-day free trial; Stripe subscription lifecycle and entitlement sync |
| AI allowance | Initial 15-response/credit allowance; atomic and tenant-safe ledger required |
| Uploads | CSV/Excel family; maximum 5 files per batch; maximum 25 MB per file |
| Dashboards | 3 dashboard types; premium dashboard cap of 20 |
| Export | Server-side PDF generation |
| Accounting integrations | QuickBooks, Xero, and Sage are later-phase work |
| Currency | No multi-currency in the initial release |
| Customer Service | Account and billing visibility only |

## 3. Technical stack

- FastAPI
- SQLModel / asynchronous SQLAlchemy
- PostgreSQL
- Alembic
- Celery + Redis
- Stripe
- AWS S3
- CSV, XLSX, and legacy XLS parsing
- JWT access/refresh tokens
- `uv`, Ruff, pytest
- Google Gemini / `google-genai` planned for Milestone 5

## 4. Completed work

### Phase 1 / authentication foundation

- Registration and email/password login
- Six-digit signup OTP
- Six-digit forgot/reset password OTP
- JWT access and refresh tokens
- Role-aware access foundation
- Business creation
- Remember Me approved
- Console email backend for development

### Milestone 1 — Business and Subscription Foundation

- Business onboarding
- Plan and subscription records
- Entitlements
- Role assignments
- Tenant isolation

### Milestone 2 — Stripe Billing

- 14-day trial
- Stripe Checkout
- Subscription lifecycle and webhooks
- Renewals and cancellations
- Billing history
- Entitlement synchronization

### Milestone 3 — Secure Uploads

- S3-backed secure upload batches
- Tenant-safe access
- 5-file and 25 MB limits
- Upload positions
- Migrations `0006_m3_secure_uploads` and `0007_m3_upload_positions`
- Unit and integration coverage

### Milestone 4 — Data Processing

- Migration `0008_m4_data_processing`
- Processing models and job lifecycle
- Bounded CSV, XLSX, and XLS parsing
- Celery dispatch and worker execution
- S3 processing artifacts
- Upload completion creates and dispatches jobs
- Deterministic dispatch timestamp behavior
- Unit and integration coverage

## 5. Key code map

| Path | Responsibility |
|---|---|
| `app/api/v1/uploads.py` | Upload API and processing-dispatch handoff |
| `app/models/processing.py` | Processing persistence models |
| `app/services/uploads.py` | Upload lifecycle orchestration |
| `app/services/report_parser.py` | CSV/XLSX/XLS parser |
| `app/services/processing.py` | Processing lifecycle and dispatch bookkeeping |
| `app/services/processing_dispatch.py` | Job dispatcher |
| `app/services/processing_artifacts.py` | Processing artifacts |
| `app/services/s3_storage.py` | S3 gateway |
| `app/workers/tasks.py` | Celery tasks |
| `alembic/versions/0008_m4_data_processing.py` | Milestone 4 migration |
| `tests/integration/test_milestone4_processing.py` | Processing integration coverage |
| `tests/unit/test_milestone4_*.py` | Contract, lifecycle, and parser tests |

## 6. Processing lifecycle

1. Authenticated tenant user creates an upload batch.
2. Client uploads permitted files.
3. Client completes the batch.
4. Upload service creates processing jobs.
5. Database transaction commits before external dispatch.
6. Dispatcher enqueues jobs.
7. Worker parses each file and records success/failure.
8. Artifacts are stored through the processing artifact service.
9. Clients retrieve batch and processing state.

Preserve commit-before-publish behavior, idempotency, tenant isolation, and bounded parsing.

## 7. Test evidence

- Ruff: passed
- Ruff format check: 83 files already formatted
- Targeted lifecycle tests: 12 passed
- Full local suite: 106 passed, 36 skipped
- Skipped tests require `RUN_INTEGRATION_TESTS=1`, PostgreSQL, and Redis
- GitHub pull-request checks: passed
- Copilot re-review: no new actionable comments

## 8. Local setup

```powershell
git switch develop
git pull --ff-only origin develop
uv sync
uv run alembic upgrade head
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

Run integration tests after PostgreSQL and Redis are available:

```powershell
$env:RUN_INTEGRATION_TESTS = "1"
uv run pytest -q tests/integration
Remove-Item Env:RUN_INTEGRATION_TESTS
```

Do not commit environment files or secrets. Development authentication email may use `EMAIL_BACKEND=console`.

## 9. Remaining roadmap

### Milestone 5 — Gemini AI and atomic credit ledger

Required:

- Gemini provider gateway
- Tenant-scoped AI/chat API
- Atomic credit reservation, consumption, and refund
- Initial 15-credit policy
- Idempotency and concurrency protection
- Usage history
- Provider failure handling
- Unit and integration tests

Suggested definition of done: concurrent requests cannot overspend credits, failures follow an approved refund policy, and all records remain tenant-isolated and auditable.

### Milestone 6 — Dashboards and PDF generation

- Three dashboard types
- Processed-data transformations
- Dashboard persistence/history
- Premium cap of 20
- Server-side PDF export
- Authorization and tests

### Milestone 7 — Admin and Customer Service operations

- Admin plan and entitlement operations
- Restricted Customer Service account/billing view
- Audit logging
- Permission tests

### Milestone 8 — Frontend integration

- Authentication and onboarding
- Billing
- Uploads and processing status
- AI chat
- Dashboards and PDF download
- Stable API contracts and error handling

### Milestone 9 — Production hardening and deployment

- Production configuration and secrets
- API/worker deployment
- Migrations
- Stripe webhooks
- S3 policies
- Production email
- Monitoring/logging
- Backups and recovery
- Security and load testing
- CI/CD and release runbooks

## 10. Recommended takeover steps

1. Confirm GitHub access.
2. Update `develop`.
3. Install dependencies.
4. Start PostgreSQL and Redis.
5. Apply migrations.
6. Run lint, formatting, unit, and integration tests.
7. Receive Stripe, AWS, and Gemini access securely.
8. Review completed milestone tests before changing behavior.

Start Milestone 5:

```powershell
git switch develop
git pull --ff-only origin develop
git switch -c milestone-5-gemini-credit-ledger
```

Recommended implementation order:

1. Ledger tables and constraints
2. Atomic reserve/consume/refund service
3. Concurrency tests
4. Gemini gateway and test double
5. Conversation/message persistence
6. Tenant-scoped API
7. Provider + ledger transaction flow
8. Usage history, errors, rate limiting, and logs
9. Integration tests

## 11. Known risks

- Guarded integration tests can be skipped accidentally
- AI-credit concurrency can overspend a basic counter
- Provider failure/refund policy must be approved
- Processing retries must remain idempotent
- Legacy XLS inputs require strict bounds
- AWS, Stripe, and Gemini credentials require secure transfer
- Backend/frontend contract drift
- Production API, workers, webhooks, migrations, and storage must deploy consistently

## 12. Git workflow

1. Start from latest `origin/develop`.
2. Create a purpose-specific feature branch.
3. Do not commit secrets or temporary patch files.
4. Run `git diff --check`, Ruff, format check, unit tests, and relevant integration tests.
5. Open PRs against `develop`.
6. Address review comments with focused commits.
7. Merge only with green checks and resolved conversations.
8. Fast-forward local `develop` after merge.
9. Delete merged feature branches locally and remotely.

## 13. Acceptance checklist

- [ ] Repository access confirmed
- [ ] `origin/main` and `origin/develop` fetch correctly
- [ ] `develop` baseline understood
- [ ] `uv sync` succeeds
- [ ] PostgreSQL and Redis start
- [ ] Alembic upgrades to head
- [ ] Ruff checks pass
- [ ] Unit tests pass
- [ ] Integration tests run with `RUN_INTEGRATION_TESTS=1`
- [ ] Upload and processing lifecycle understood
- [ ] Stripe access transferred securely
- [ ] AWS S3 access transferred securely
- [ ] Gemini access transferred securely
- [ ] Approved product constraints accepted
- [ ] Milestones 5–9 ownership accepted
- [ ] No secrets included in Git or this document

## 14. Sign-off

**Outgoing developer:** Nayeem Hossen Jim  
**Date:** 04.08.2026

**Incoming developer:** Latif  
**Date:** 04.08.2026

---

### Final baseline

```text
Branch: develop
Commit: 38f2816
Tracking: develop...origin/develop
Working tree: clean
Milestone 4 local branch: deleted
Milestone 4 remote branch: deleted
```

---

<a id="3-master-implementation-plan"></a>
# 3. Master Implementation Plan

# Spike Technology Backend - Implementation Plan

**Last updated:** 2026-08-11

## 1. Project status

The backend implementation through **Milestone 7** is complete.

Current validated checkpoint:

| Item | Value |
|---|---|
| Branch | `develop` |
| Milestone 7 merge commit | `2c5ee900ba987393f4aaa4986b780798962079be` |
| Alembic head | `0020_gap06_v1_entitlements` |
| Full test suite | **478 passed** |
| Full integration suite | **98 passed** |
| Milestone 7 integration suite | **22 passed** |

The complete migration chain has also been proven against a fresh PostgreSQL
database from the initial migration through `0020`.

---

# 2. Completed implementation

## Phase 1 / Milestone 0 - Authentication Foundation

**Status: COMPLETE**

Implemented:

- user registration;
- email verification using six-digit OTP;
- login using email and password;
- JWT access tokens;
- refresh-token sessions;
- Remember Me support;
- password reset using six-digit OTP;
- authentication rate limiting;
- session hardening;
- active-user validation;
- verified-user enforcement;
- secure password hashing;
- development console email delivery.

---

## Milestone 1 - Business and Subscription Foundation

**Status: COMPLETE**

Implemented:

- business onboarding;
- business ownership;
- tenant role assignments;
- tenant isolation;
- plans;
- subscriptions;
- entitlements;
- subscription access evaluation;
- tenant-scoped authorization;
- platform-role separation from tenant-role access.

---

## Milestone 2 - Stripe Billing

**Status: COMPLETE**

Implemented:

- 14-day trial;
- Stripe Checkout;
- Stripe subscription synchronization;
- webhook processing;
- renewal handling;
- cancellation handling;
- billing history;
- entitlement synchronization;
- Stripe test-mode integration;
- idempotent webhook behavior;
- Stripe-authoritative paid subscription lifecycle.

---

## Milestone 3 - Secure Uploads

**Status: COMPLETE**

Implemented:

- secure report upload model;
- tenant-owned report files;
- tenant-isolated storage keys;
- S3 upload gateway abstraction;
- upload request ordering;
- maximum file-size validation;
- maximum files-per-request validation;
- storage configuration validation.

Current development configuration intentionally keeps S3 disabled until a
private production bucket is configured.

---

## Milestone 4 - Data Processing

**Status: COMPLETE**

Implemented:

- durable processing jobs;
- job dispatch;
- Celery-based processing;
- processing state transitions;
- report parsing;
- processing artifacts;
- tenant isolation;
- failure handling;
- processing-job persistence.

---

## Milestone 5 - Gemini AI, Credits and Conversations

**Status: COMPLETE**

Implemented:

- Google Gemini gateway;
- AI execution services;
- atomic AI-credit account;
- immutable reservation ledger;
- reserve / consume / release lifecycle;
- concurrency protection;
- entitlement-driven limits;
- AI conversations;
- AI messages;
- tenant isolation;
- execution recovery/lease handling;
- idempotent AI execution behavior.

---

## Milestone 6 - Dashboards and PDF

**Status: COMPLETE**

Implemented:

- dashboard foundation;
- dashboard types;
- dashboard management;
- dashboard snapshots;
- report-derived dashboard data;
- CSV/XLSX-related reporting support;
- server-side PDF generation;
- PDF export endpoints;
- production-compatible Linux PDF runtime dependencies.

---

## Milestone 7 - Admin Operations

**Status: COMPLETE**

### Platform roles

Implemented:

- `super_admin`;
- `customer_service`;
- strict separation from customer `user` accounts.

### Administrative reads

Implemented:

- `GET /api/v1/admin/users`;
- `GET /api/v1/admin/businesses`;
- safe customer/business support metadata;
- Customer Service cannot enumerate platform staff;
- Super Admin may enumerate platform roles.

### Immutable administrative audit

Implemented:

- `admin_audit_events`;
- append-only database protection;
- UPDATE/DELETE blocking trigger;
- actor snapshots;
- request IDs;
- safe bounded metadata;
- sensitive metadata rejection;
- atomic audit + privileged mutation behavior.

### Account lifecycle

Implemented:

- Super Admin customer suspension;
- Super Admin customer reactivation;
- Customer Service mutation denial;
- platform-account target protection;
- self-target protection;
- refresh-token revocation;
- `auth_session_version`;
- permanent invalidation of old JWT sessions;
- fresh authentication required after reactivation;
- immutable lifecycle audit entries.

### Subscription support visibility

Implemented:

- `GET /api/v1/admin/businesses/{business_id}/subscription`;
- Super Admin and Customer Service read access;
- safe plan/subscription metadata;
- safe effective entitlements;
- Stripe-managed status abstraction;
- no Stripe customer/subscription/price IDs exposed;
- no administrative local subscription mutation surface.

### AI credit administration

Implemented:

- append-only `ai_credit_adjustment_ledger_entries`;
- Super Admin-only credit adjustment API;
- signed positive/negative adjustments;
- closed reason codes;
- mandatory idempotency key;
- SHA-256 digest storage instead of raw key storage;
- same-request replay handling;
- conflicting replay rejection;
- tenant/business serialization;
- negative-credit floor protection;
- reserved-credit protection;
- consumed-credit protection;
- immutable adjustment rows;
- atomic adjustment + admin audit event;
- rollback on audit failure;
- Customer Service mutation denial.

---

## Figma-contract gaps / V1 Contract Completion

**Status: COMPLETE**

Implemented:

- report-grounded dashboard insight and AI analysis contracts;
- self-service profile fields;
- verified account credential and deletion lifecycle;
- email two-step verification challenges;
- aligned approved v1 plan and trial entitlements;
- paginated tenant upload history for the Figma report-history views.

### Final V1 release validation

Validated:

- M7 integration: **22 passed**;
- complete integration: **98 passed**;
- complete project: **478 passed**;
- migration upgrade: empty DB -> `0020`;
- migration downgrade: `0020` -> `0015`;
- migration re-upgrade: `0015` -> `0020`;
- admin route surface verified;
- secret-like Git additions scan passed;
- Ruff passed;
- formatting passed;
- Git diff validation passed.

---

# 3. Current development environment

Current local-development behavior:

- `APP_ENV=development`;
- PostgreSQL through Docker Compose;
- Redis through Docker Compose;
- FastAPI through Docker Compose;
- Celery worker through Docker Compose;
- console email backend;
- Stripe test mode;
- S3 disabled until production private storage is configured.

These items are environment/deployment configuration, not incomplete
Milestones 0-7 backend functionality.

---

# 4. Immediate next actions

Before starting production-readiness implementation:

- [ ] keep `develop` clean;
- [ ] commit this documentation checkpoint;
- [ ] optionally recreate a local development Super Admin after the Docker
      database reset;
- [ ] verify API health endpoint locally;
- [ ] verify Celery worker connectivity;
- [ ] create branch `milestone-8-production-readiness`;
- [ ] capture a fresh baseline test result;
- [ ] begin Milestone 8 Stage 1.

---

# 5. Milestone 8 - Production Readiness & Deployment

**Status: NEXT**

Milestone 8 should turn the feature-complete backend into a secure,
recoverable, observable and deployable production service.

---

## Stage 8.1 - Environment and Secret Hardening

### Work

- [ ] define development environment;
- [ ] define test environment;
- [ ] define staging environment;
- [ ] define production environment;
- [ ] audit `.env.example`;
- [ ] document every required environment variable;
- [ ] remove insecure development defaults from production paths;
- [ ] generate fresh production JWT secrets;
- [ ] configure production CORS;
- [ ] configure trusted hosts;
- [ ] configure secure-cookie policy;
- [ ] require HTTPS assumptions in production;
- [ ] verify secrets cannot appear in application logs;
- [ ] add startup validation for required production settings.

### Exit criteria

- production cannot start with missing critical configuration;
- production cannot silently use development values;
- no production secret is stored in Git.

---

## Stage 8.2 - Production S3 Object Storage

### Work

- [ ] create private S3 bucket;
- [ ] configure bucket region;
- [ ] configure least-privilege IAM;
- [ ] configure tenant-isolated object prefixes;
- [ ] enable application S3 integration;
- [ ] validate uploads;
- [ ] validate downloads;
- [ ] validate signed URL expiration;
- [ ] validate cross-tenant denial;
- [ ] configure retention/lifecycle policy;
- [ ] configure encryption;
- [ ] add production-like integration tests.

### Exit criteria

Real files can be uploaded and retrieved securely without exposing the bucket
publicly or allowing cross-tenant access.

---

## Stage 8.3 - Production Email

### Work

- [ ] choose/configure production email provider;
- [ ] verify sending domain;
- [ ] configure SPF;
- [ ] configure DKIM;
- [ ] configure DMARC where appropriate;
- [ ] configure sender identity;
- [ ] test signup OTP;
- [ ] test password-reset OTP;
- [ ] test expiry handling;
- [ ] configure delivery-failure handling;
- [ ] configure bounce handling;
- [ ] ensure OTP values are never logged.

### Exit criteria

Authentication email workflows operate reliably outside the console backend.

---

## Stage 8.4 - Stripe Production Readiness

### Work

- [ ] create/verify live Stripe products;
- [ ] create/verify live Stripe prices;
- [ ] configure production webhook endpoint;
- [ ] configure live-mode credentials securely;
- [ ] verify webhook signature validation;
- [ ] verify webhook idempotency;
- [ ] validate checkout;
- [ ] validate trial conversion;
- [ ] validate renewal;
- [ ] validate cancellation;
- [ ] validate failed payment;
- [ ] validate subscription-access transitions;
- [ ] confirm Stripe remains lifecycle source of truth.

### Exit criteria

Billing lifecycle works end-to-end using production-equivalent Stripe
configuration.

---

## Stage 8.5 - Production PostgreSQL and Redis

### Work

- [ ] provision managed/production PostgreSQL;
- [ ] provision production Redis;
- [ ] configure TLS where supported;
- [ ] configure connection limits;
- [ ] configure PostgreSQL backups;
- [ ] configure retention;
- [ ] document restore workflow;
- [ ] run restore test;
- [ ] validate Alembic against staging;
- [ ] document deployment migration procedure;
- [ ] document rollback procedure;
- [ ] configure Redis persistence according to workload requirements.

### Exit criteria

Production data stores are secured, monitored and recoverable from backups.

---

## Stage 8.6 - CI/CD

### Work

- [ ] create CI workflow;
- [ ] run Ruff in CI;
- [ ] run format checks;
- [ ] run unit tests;
- [ ] run PostgreSQL integration tests;
- [ ] run Redis integration tests;
- [ ] verify Alembic heads;
- [ ] build Docker images;
- [ ] scan dependencies;
- [ ] scan images;
- [ ] tag versioned images;
- [ ] deploy automatically to staging;
- [ ] require controlled production promotion;
- [ ] preserve deployment logs.

### Exit criteria

Code cannot reach production without automated quality gates.

---

## Stage 8.7 - Observability and Operations

### Work

- [x] structured JSON production logs;
- [x] request/correlation IDs;
- [x] error tracking;
- [x] API health endpoint;
- [x] API readiness endpoint;
- [x] worker monitoring;
- [x] Celery failure monitoring;
- [x] PostgreSQL monitoring;
- [x] Redis monitoring;
- [x] latency metrics;
- [x] HTTP error-rate metrics;
- [x] job failure metrics;
- [x] alerting;
- [x] operational runbook.

### Exit criteria

Operational failures can be detected and diagnosed without direct server
inspection.

**Implementation status: COMPLETE.**

The backend now provides structured production logs, correlation IDs, safe
exception/error signals, protected Prometheus-compatible API/infrastructure/job
metrics, version-controlled alert rules, and an operational response runbook.
Loading the alert rules into the production monitoring platform and routing
notifications to the approved operational destination remain deployment
activation tasks for staging/production.

---

## Stage 8.8 - Security Release Audit

### Work

- [x] dependency vulnerability scan;
- [x] authentication regression;
- [x] authorization regression;
- [x] tenant-isolation regression;
- [x] admin permission-matrix regression;
- [x] JWT/session-revocation regression;
- [x] Stripe webhook replay tests;
- [x] upload-abuse tests;
- [x] rate-limit tests;
- [x] sensitive-response inspection;
- [x] sensitive-log inspection;
- [x] AI-credit concurrency tests;
- [x] audit immutability tests;
- [x] secrets scan.

### Exit criteria

No known high-risk authorization, tenancy, credential or administrative
security defect remains.

---

## Stage 8.9 - Performance and Reliability

### Work

- [x] API load testing;
- [x] authentication load testing;
- [x] upload load testing;
- [x] database concurrency testing;
- [x] AI-credit concurrency testing;
- [x] Celery throughput testing;
- [x] large-report processing testing;
- [x] dashboard-generation testing;
- [x] PDF-generation testing;
- [x] failure/retry behavior validation;
- [x] establish acceptable latency targets.

### Exit criteria

The backend meets agreed performance targets without correctness failures.

**Implementation status: COMPLETE.**

Stage 8.9 established local engineering performance baselines for API,
authentication, upload, database, AI-credit, Celery, report-processing,
dashboard and PDF workloads. Correctness and concurrency invariants remained
intact throughout the validated workloads. The accepted targets and measured
evidence are documented in `docs/performance_targets.md`.

These measurements are engineering release baselines, not public production
SLAs. Staging and production capacity must be revalidated on their actual
infrastructure and representative customer workloads.

---

## Stage 8.10 - Frontend End-to-End Staging Validation

### Required flows

- [ ] registration;
- [ ] six-digit verification OTP;
- [ ] login;
- [ ] Remember Me;
- [ ] forgot/reset password OTP;
- [ ] business onboarding;
- [ ] trial state;
- [ ] checkout;
- [ ] report upload;
- [ ] report processing;
- [ ] AI conversation;
- [ ] AI-credit consumption;
- [ ] dashboards;
- [ ] dashboard snapshots;
- [ ] report/PDF exports;
- [ ] billing visibility;
- [ ] account cancellation;
- [ ] Super Admin flows;
- [ ] Customer Service flows.

### Exit criteria

The frontend and backend complete every supported v1 user journey in staging.

**Ownership:** frontend implementation and frontend API integration are owned
by the frontend team. The backend team provides the staging API, API contract,
backend fixes and integration support. This stage remains pending until the
frontend team confirms the required v1 journeys.

---

## Stage 8.11 - Staging and UAT

The backend team owns backend staging deployment, migrations, infrastructure
validation, backend smoke testing, rollback and database restore testing.
Frontend E2E execution and UAT sign-off are shared/external acceptance gates.

### Work

- [ ] deploy staging;
- [ ] configure staging domain;
- [ ] configure staging TLS;
- [ ] run migrations;
- [ ] seed only approved test data;
- [ ] execute full smoke suite;
- [ ] execute E2E suite;
- [ ] execute UAT;
- [ ] resolve release-blocking defects;
- [ ] test rollback;
- [ ] test database restore.

### Exit criteria

Staging behaves like production and passes final acceptance testing.

---

## Stage 8.12 - Production Launch

### Work

- [ ] final production checklist;
- [ ] verify backups;
- [ ] verify DNS;
- [ ] verify TLS;
- [ ] verify production secrets;
- [ ] execute migrations;
- [ ] deploy API;
- [ ] deploy worker;
- [ ] verify Redis;
- [ ] verify PostgreSQL;
- [ ] verify email;
- [ ] verify S3;
- [ ] verify Stripe webhook;
- [ ] run production smoke test;
- [ ] monitor error rates;
- [ ] monitor worker queues;
- [ ] monitor database;
- [ ] document deployed release;
- [ ] maintain rollback readiness.

### Exit criteria

The production environment is healthy, monitored, backed up and serving the
validated v1 workflows.

---

# 6. Milestone 8 definition of done

Milestone 8 is complete only when:

- [ ] production configuration is hardened;
- [ ] production secrets are securely managed;
- [ ] S3 integration is live and secure;
- [ ] production email delivery is verified;
- [ ] Stripe production configuration is verified;
- [ ] production PostgreSQL is backed up;
- [ ] restore procedure is tested;
- [ ] production Redis is operational;
- [ ] CI/CD release gates are active;
- [ ] monitoring and alerting are active;
- [ ] security release audit passes;
- [x] performance validation passes;
- [ ] frontend/backend E2E passes in staging;
- [ ] UAT passes;
- [ ] rollback procedure is tested;
- [ ] production smoke testing passes.

---

# 7. Future work after Milestone 8

Future product expansion should be planned separately after the validated v1
production release.

Possible future milestones must not be mixed into Milestone 8 unless they are
required for production correctness, security or operations.

---

<a id="4-v1-client-handover-summary"></a>
# 4. V1 Client Handover Summary

# Spike Technology Backend - V1 Client Handover

**Date:** August 2026
**Status:** V1 Backend Feature Complete & Validated
**Target:** Frontend Integration & Pre-Deployment Sign-off

---

## 1. Executive Summary

The Spike Technology backend is now **100% feature-complete** for the agreed V1 scope. The codebase has been rigorously tested, audited for security, and is ready to be consumed by the frontend application. 

Deployment to production infrastructure (Milestone 8) is scheduled as a separate, subsequent phase pending your review of this codebase and frontend integration.

### Quality & Validation Metrics
- **Automated Tests:** 478 passing tests (unit & integration).
- **Code Quality:** 100% compliant with strict `ruff` linting and formatting rules.
- **Database Migrations:** Clean, linear Alembic migration history up to head `0020_gap06_v1_entitlements`.
- **Security:** Tenant isolation, bounded file parsing, stateless JWTs, and secure password hashing are fully implemented.

---

## 2. V1 Capabilities Delivered

### Authentication & Security
- Secure Email/Password registration and login.
- Six-digit OTP verification for signup, email changes, and password resets.
- Secure, rotating Refresh Tokens and short-lived stateless Access Tokens (JWT).
- Optional Email Two-Step Verification for enhanced account security.

### Multi-Tenancy, Billing & Subscriptions
- Strict tenant data isolation (one business per account context).
- Stripe Checkout integration for subscription upgrades.
- Automated webhooks for renewals, cancellations, and invoice tracking.
- Feature entitlements (AI limits, Dashboard limits) synced automatically based on active subscriptions or 14-day trials.

### Secure File Uploads & Processing
- Direct-to-S3 secure uploads using Presigned POST URLs.
- Strict validation (CSV, XLS, XLSX) with 25MB file size and batch limits.
- Background asynchronous data processing via Celery and Redis.
- Bounded parsing limits to prevent memory exhaustion or malicious file attacks.

### AI, Dashboards & PDF Export
- Google Gemini AI integration with a robust, atomic, and tenant-safe credit ledger (prevents overspending).
- Three comprehensive dashboard types (Executive Summary, Financial Performance, Operational KPI).
- Server-side PDF report generation using WeasyPrint.
- Report-grounded insights combining your uploaded data with AI analysis.

### Platform Administration
- Super Admin and Customer Service roles with restricted, safe read-only access to customer support data.
- Immutable administrative audit logging for privileged actions.
- Account suspension, reactivation, and manual AI-credit adjustment endpoints.

---

## 3. Local Evaluation & Quick Start

To evaluate the APIs locally without needing live cloud infrastructure, the backend is configured to mock or gracefully degrade external services (e.g., printing OTPs to the console instead of emailing them, and using a local SQLite/PostgreSQL setup).

### Prerequisites
- Docker Desktop (for PostgreSQL & Redis)
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Python 3.12

### Spin-up Instructions
1. **Prepare Environment:**
   ```bash
   cp .env.example .env
   # Generate a local JWT secret and place it in .env
   uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
2. **Install Dependencies:**
   ```bash
   uv sync --frozen --extra dev
   ```
3. **Start Databases (Docker):**
   ```bash
   docker compose up -d postgres redis
   ```
4. **Run Database Migrations:**
   ```bash
   uv run alembic upgrade head
   ```
5. **Start the API & Worker:**
   ```bash
   # Terminal 1: API
   uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   
   # Terminal 2: Background Worker
   uv run celery -A app.workers.celery_app:celery_app worker --loglevel=INFO
   ```

The Interactive API Documentation will now be available at: **[http://localhost:8000/docs](http://localhost:8000/docs)**.

---

## 4. Next Steps (Post-Handover)

Following frontend integration and your approval of this V1 codebase, we will proceed to **Milestone 8: Production Readiness & Deployment**. This will involve:
- Provisioning live AWS S3, Stripe Production, and Google Gemini infrastructure.
- Setting up CI/CD deployment pipelines.
- Configuring managed PostgreSQL databases and Redis clusters.
- Setting up DNS, SSL, and operational monitoring.

---

<a id="5-operational-runbook--observability"></a>
# 5. Operational Runbook & Observability

# Spike Technology Backend Operational Runbook

## Purpose

This runbook describes the first-response procedure for production operational
failures in the Spike Technology backend.

It is designed to work with the backend's structured JSON logs, request IDs,
health endpoints, Prometheus-compatible metrics, Celery worker health, and
durable report-processing state.

Do not place credentials, bearer tokens, customer payloads, uploaded report
contents, OTP values, JWTs, or provider secrets in incident notes.

## Health endpoints

### Liveness

`GET /api/v1/health/live`

A successful response proves that the API process can accept HTTP requests.

Liveness does not prove that PostgreSQL, Redis, Celery, S3, email, Stripe, or
Gemini are healthy.

### Readiness

`GET /api/v1/health/ready`

A successful response proves that the API can currently reach PostgreSQL and
Redis.

A `503` response means at least one required dependency is unavailable.

## Metrics access

Operational metrics are exposed at:

`GET /api/v1/health/metrics`

Production requires `METRICS_BEARER_TOKEN`.

Send the token using the standard Authorization header:

`Authorization: Bearer <metrics-token>`

Never place the metrics token in a URL, query parameter, source-control file,
log message, screenshot, or incident document.

The metrics endpoint intentionally returns `404` when authentication fails.

## Core metrics

### API

- `spike_http_requests_total`
- `spike_http_request_duration_seconds`
- `spike_unhandled_errors_total`

HTTP metrics use route templates rather than raw request URLs so that
user-provided paths and query values are not exported as metric labels.

### PostgreSQL

- `spike_postgresql_available`
- `spike_postgresql_probe_duration_seconds`

### Redis

- `spike_redis_available`
- `spike_redis_probe_duration_seconds`

### Report processing

- `spike_report_processing_metrics_available`
- `spike_report_processing_jobs`
- `spike_report_processing_failed_jobs`
- `spike_report_processing_retrying_jobs`
- `spike_report_processing_stale_leases`

## Structured logs

Production application logs are JSON.

Useful correlation fields include:

- `request_id`
- `route`
- `status_code`
- `duration_ms`
- `outcome`
- `error_code`
- `exception_type`
- `task_id`
- `job_id`
- `report_upload_id`
- `attempt_count`
- `phase`

For an API incident, begin with the affected request's `X-Request-ID` response
header and search structured logs for the same `request_id`.

Exception messages are intentionally not serialized by the application JSON
formatter because provider or user data may be sensitive.

## Alert response

Prometheus-compatible alert rules are stored in:

`ops/prometheus/alerts.yml`

The production monitoring platform must load these rules and route alert
notifications to the approved operational notification destination.

### SpikeUnhandledApplicationErrors

1. Confirm that `spike_unhandled_errors_total` increased.
2. Find `http_request_failed` events in structured API logs.
3. Correlate using `request_id`, route, and exception type.
4. Determine whether the error began after a release or configuration change.
5. Check PostgreSQL and Redis availability.
6. Escalate immediately if authentication, authorization, tenant isolation,
   billing, credit accounting, or data integrity may be affected.

### SpikeHighHttpServerErrorRate

1. Confirm the affected routes using HTTP metrics.
2. Check `http_request_completed` and `http_request_failed` logs.
3. Check PostgreSQL and Redis alerts.
4. Check worker health if affected routes depend on asynchronous processing.
5. If the condition began immediately after deployment, prepare rollback.

### SpikePostgresqlUnavailable

1. Confirm `/health/ready` is failing.
2. Check `spike_postgresql_available`.
3. Check database service health and network reachability.
4. Check recent credential, secret, DNS, firewall, or deployment changes.
5. Do not repeatedly run migrations while database state is uncertain.
6. Escalate before performing restore or destructive database operations.

### SpikeRedisUnavailable

1. Confirm `/health/ready` is failing.
2. Check `spike_redis_available`.
3. Check Redis service health and connectivity.
4. Check Celery broker and result-backend connectivity.
5. Check persistence and resource exhaustion before restarting Redis.

### SpikeProcessingMetricsUnavailable

1. Check PostgreSQL availability first.
2. Verify that the API can query report-processing tables.
3. Review metric-loader warning logs.
4. Treat missing processing metrics as unknown state, not as zero failures.

### SpikeStaleProcessingLeases

1. Check Celery worker health.
2. Check queued, processing, retrying, and failed job metrics.
3. Search worker logs using `job_id` and `task_id`.
4. Determine whether workers stopped, lost broker connectivity, exceeded
   execution time, or entered retry behavior.
5. Do not manually mutate durable job state without verifying retry and lease
   semantics.

## Celery worker checks

The production worker must respond successfully to the configured Celery ping
health check.

When a worker is unhealthy:

1. Check Redis first because it is the Celery broker.
2. Check worker process/container health.
3. Check recent task failures and retry logs.
4. Check stale processing leases.
5. Restart only after confirming that retry and lease behavior will not cause
   duplicate unsafe work.

## Sensitive-data handling

Never copy the following into alerts or incident documents:

- Authorization headers
- JWT access or refresh tokens
- OTP values
- password-reset secrets
- Stripe secrets or webhook secrets
- Gemini credentials
- AWS credentials
- metrics bearer tokens
- uploaded report contents
- raw customer query strings

Use IDs and safe structured metadata for correlation.

## Recovery and rollback

For a release-related incident:

1. Stop further production promotion.
2. Preserve logs and monitoring evidence.
3. Identify the last known-good release.
4. Determine whether database migrations are backward compatible.
5. Roll back application code only when doing so is safe for the current
   database schema.
6. Re-run liveness, readiness, metrics, worker, and smoke checks.
7. Confirm error rates return to normal before closing the incident.

Database restore and migration rollback require their dedicated production
procedures and must not be improvised during an incident.

## Incident closure

Before closing an operational incident:

1. Confirm liveness and readiness.
2. Confirm PostgreSQL and Redis availability metrics.
3. Confirm worker health.
4. Confirm no new unexpected unhandled errors.
5. Confirm stale processing leases have been resolved.
6. Record the affected release and root cause.
7. Record any corrective action or follow-up security/performance work.

Stage 8.9 will establish explicit performance and latency targets. Alert
thresholds based on those targets should be added only after those targets are
measured and approved.

---

<a id="6-staging-deployment--recovery-runbook"></a>
# 6. Staging Deployment & Recovery Runbook

# Staging Deployment and Recovery Runbook

## Purpose

This runbook defines backend responsibilities for Milestone 8 Stage 8.11.

The backend team owns:

- staging backend deployment;
- API and worker deployment;
- PostgreSQL and Redis connectivity;
- migrations;
- backend smoke validation;
- backend release-blocker fixes;
- application rollback validation;
- PostgreSQL backup and restore validation.

The frontend team owns frontend implementation and frontend API integration.

Frontend E2E execution and UAT sign-off remain shared/external acceptance
gates and must not be marked complete until those teams confirm success.

## Frontend API handoff

The backend exposes:

- API prefix: `/api/v1`
- Swagger UI: `/docs`
- OpenAPI JSON: `/api/v1/openapi.json`
- liveness: `/api/v1/health/live`
- readiness: `/api/v1/health/ready`

The staging frontend origin must be configured in:

- `FRONTEND_URL`
- `CORS_ORIGINS`

The staging API hostname must be configured in:

- `TRUSTED_HOSTS`

## Staging configuration

Use `.env.staging.example` as the staging configuration checklist.

Staging uses:

`APP_ENV=staging`

Staging must use production-like infrastructure controls:

- HTTPS frontend URL;
- HTTPS Stripe redirect URLs;
- non-localhost PostgreSQL;
- non-localhost Redis;
- non-localhost Celery broker/result backend;
- explicit trusted hosts;
- no wildcard CORS;
- private S3 bucket;
- SES email delivery;
- Gemini enabled;
- metrics authentication;
- non-placeholder JWT credentials.

Stripe staging configuration is intentionally different from production.

Staging must use:

- Stripe test secret keys;
- Stripe test webhook secrets;
- Stripe test Price IDs.

Production must continue to use Stripe live mode.

Never reuse production secrets, production billing objects, production S3
buckets, or production customer data in staging.

## Staging deployment sequence

Use this order:

1. Confirm the intended Git commit passed CI.
2. Record the release Git commit.
3. Build an immutable backend image.
4. Record the image digest.
5. Provision staging PostgreSQL.
6. Provision staging Redis.
7. Provision the private staging S3 bucket.
8. Verify the SES staging sender.
9. Configure the Stripe test webhook.
10. Configure the staging Gemini credential.
11. Configure staging API DNS.
12. Configure staging TLS.
13. Configure the frontend staging origin.
14. Store staging secrets in the deployment platform secret manager.
15. Verify PostgreSQL connectivity.
16. Verify Redis connectivity.
17. Take a pre-deployment database backup.
18. Run `alembic upgrade head`.
19. Run `alembic current`.
20. Deploy the API.
21. Deploy the worker using the same image.
22. Verify liveness.
23. Verify readiness.
24. Verify Celery worker health.
25. Verify protected metrics.
26. Execute the backend smoke suite.
27. Provide the staging API URL and OpenAPI contract to the frontend team.

## Approved staging data

Only synthetic or explicitly approved test data may be used.

Do not copy production customer information into staging.

Do not copy:

- production user accounts;
- production uploaded reports;
- production authentication credentials;
- production billing records;
- production AI conversations;
- production admin audit records.

Staging accounts must be clearly identifiable as test accounts.

## PostgreSQL backup

Prefer the managed PostgreSQL provider's snapshot system when available.

For a portable logical backup, `pg_dump` may be used.

The application uses a SQLAlchemy URL such as:

`postgresql+asyncpg://...`

PostgreSQL command-line tools require a normal PostgreSQL connection URL.

Example operator procedure:

    export POSTGRES_BACKUP_URL='postgresql://USER:PASSWORD@HOST:5432/DATABASE'

    pg_dump       --format=custom       --no-owner       --no-privileges       --file=spike-staging.dump       "$POSTGRES_BACKUP_URL"

    unset POSTGRES_BACKUP_URL

Backups must be stored in encrypted, access-controlled storage.

Never commit a database dump.

## Database restore validation

Never restore a test backup over the active staging database.

Use a disposable restore database.

Example:

    export POSTGRES_RESTORE_URL='postgresql://USER:PASSWORD@HOST:5432/RESTORE_DATABASE'

    pg_restore       --exit-on-error       --no-owner       --no-privileges       --dbname="$POSTGRES_RESTORE_URL"       spike-staging.dump

    unset POSTGRES_RESTORE_URL

After restoration:

1. point a temporary backend validation process at the restored database;
2. run `alembic current`;
3. verify the expected migration revision;
4. run database-backed smoke checks;
5. verify representative tenant-owned records;
6. record the restore result;
7. destroy the disposable restore database.

A backup is not considered validated until restore validation succeeds.

## Application rollback

Prefer application-image rollback instead of immediately downgrading the
database.

Rollback test procedure:

1. record the current image digest;
2. record the current migration revision;
3. deploy the new staging release;
4. complete smoke validation;
5. redeploy the last known-good API image;
6. redeploy the last known-good worker image;
7. verify liveness;
8. verify readiness;
9. verify worker health;
10. run backend smoke validation;
11. redeploy the intended release;
12. repeat health validation.

## Migration rollback validation

All current Alembic revisions provide downgrade functions.

However, migration downgrade testing must use a disposable database restored
from staging backup data.

For a one-revision staging rollback test:

    alembic current
    alembic downgrade -1
    alembic current
    alembic upgrade head
    alembic current

After the downgrade/re-upgrade cycle, execute the relevant database tests and
verify data invariants.

Do not automatically run `alembic downgrade` against production during an
incident.

Database compatibility and backup state must be evaluated first.

## Backend smoke validation

At minimum verify:

1. `/api/v1/health/live` returns HTTP 200.
2. `/api/v1/health/ready` returns HTTP 200.
3. the Celery worker responds to `inspect ping`.
4. `/api/v1/openapi.json` is reachable.
5. `/docs` is reachable.
6. registration API flow works.
7. authentication API flow works.
8. business onboarding works.
9. S3 upload initiation works.
10. report processing completes successfully.
11. AI conversation execution works.
12. AI-credit consumption is correct.
13. dashboard generation works.
14. dashboard snapshot generation works.
15. PDF export works.
16. Stripe TEST Checkout creation works.
17. Stripe TEST webhook processing works.
18. SES staging email delivery works.
19. authenticated metrics access works.

Frontend browser E2E validation is a separate gate.

## Release blockers

The following block staging acceptance:

- migration failures;
- readiness failures;
- unavailable worker;
- PostgreSQL instability;
- Redis instability;
- failed tenant isolation;
- failed S3 upload processing;
- failed SES delivery;
- failed Stripe test webhook processing;
- failed Gemini processing;
- incorrect AI-credit accounting;
- failed dashboard generation;
- failed PDF generation;
- failed database restore validation;
- unsafe application rollback.

## Evidence to retain

Record:

- Git commit;
- image digest;
- migration revision;
- staging deployment timestamp;
- staging backend domain;
- liveness result;
- readiness result;
- worker health result;
- backend smoke result;
- backup identifier;
- restore-test result;
- rollback-test result;
- frontend E2E status;
- UAT status;
- unresolved release blockers.

Never record secret values in release evidence.

---

<a id="7-performance--reliability-baselines"></a>
# 7. Performance & Reliability Baselines

# Performance and Reliability Baselines

## Purpose

This document records the engineering baselines established during
Milestone 8 Stage 8.9.

The figures below are release-validation targets for the tested local
Docker/PostgreSQL/Redis environment. They are not public customer SLAs and
must be revalidated in staging and production using representative traffic,
infrastructure sizing and customer report files.

Client-to-S3 object-transfer time and third-party provider latency are not
included in API command-acceptance targets.

## Accepted engineering targets

| Workload | Stage 8.9 engineering target |
|---|---|
| Normal authenticated API reads | p95 <= 500 ms at the validated baseline load |
| Authentication | preserve correctness under the validated concurrent authentication load; password-hash-backed login is assessed separately from normal read latency |
| Upload API command acceptance | p95 <= 500 ms, excluding direct client-to-object-storage transfer |
| Database concurrency | zero lost updates, invalid state transitions or pool correctness failures under validated concurrency |
| AI-credit reserve | p95 <= 350 ms under the validated 8-operation concurrency workload |
| AI-credit release | p95 <= 200 ms under the validated 8-operation concurrency workload |
| Celery lightweight-task throughput | >= 500 completed tasks/s for the validated 240-task workload, p95 <= 250 ms |
| 50,000-row CSV processing | <= 1.0 s locally and >= 50,000 rows/s |
| 10,000-record dashboard snapshot | create and identical replay each <= 250 ms locally |
| Dashboard PDF rendering | p95 <= 750 ms after runtime initialization |
| Failure/retry behavior | no duplicate terminal effects; retries preserve durable job identity and bounded state transitions |

## Validation evidence

### API, authentication, upload and database

Stage 8.9A through Stage 8.9C validated API load, authentication load,
upload behavior and database concurrency against the isolated local
PostgreSQL/Redis test environment.

Those runs completed without correctness failures.

Their exact terminal percentiles were not persisted in the repository at
Stage 8.9 closure, so this document intentionally does not reconstruct or
invent numeric results that are no longer authoritative.

The general API latency targets above therefore remain the approved
engineering baselines for subsequent staging validation.

### AI-credit concurrency

Existing concurrency contracts passed for:

- preventing credit overspend;
- same-idempotency-key serialization;
- mutually exclusive terminal consume/release transitions.

Measured temporary benchmark:

- reserve workload: 60 operations, concurrency 8;
- reserve throughput: 88.44 operations/s;
- reserve p50: 56.72 ms;
- reserve p95: 272.40 ms;
- reserve p99: 300.88 ms;
- release workload: 60 operations, concurrency 8;
- release throughput: 201.91 operations/s;
- release p50: 26.70 ms;
- release p95: 114.48 ms;
- release p99: 122.11 ms.

### Celery throughput

The authoritative Celery benchmark was executed from the API container so
the producer, Redis broker/result backend and worker shared the intended
Docker network path.

Validated workload:

- tasks submitted: 240;
- tasks completed: 240;
- failures: 0;
- enqueue rate: 1,417.42 tasks/s;
- completion rate: 787.70 tasks/s;
- p50 completion latency: 112.07 ms;
- p95 completion latency: 129.47 ms;
- p99 completion latency: 136.28 ms;
- queue depth after completion: 0.

An earlier host-side attempt was excluded because the Windows producer was
connected to a separate Redis instance from the Docker worker. That
environment mismatch was diagnosed and the stale benchmark queue was
removed without affecting Docker Redis.

### Large-report processing

Validated CSV parser ceiling:

- maximum rows per sheet: 100,000.

Representative benchmark:

- source rows: 50,000;
- source bytes: 1,391,423;
- normalized artifact bytes: 602,821;
- profile artifact bytes: 1,233;
- processing time: 0.480 s;
- processing throughput: 104,124.97 rows/s;
- final durable processing state: completed.

### Dashboard generation

Representative immutable snapshot benchmark:

- sources: 2;
- rows per source: 5,000;
- total records: 10,000;
- materialization time: 0.104 s;
- identical replay time: 0.099 s;
- source artifact reads: 8.

Dashboard concurrency regression also confirmed that concurrent creates
cannot exceed the configured entitlement limit and identical concurrent
snapshot requests persist only one logical snapshot.

### PDF generation

Runtime:

- WeasyPrint 69.0;
- Linux API container.

Representative benchmark:

- iterations: 8;
- warm-up: 499.83 ms;
- p50: 490.04 ms;
- p95: 515.38 ms;
- p99: 515.38 ms;
- maximum: 515.38 ms;
- mean: 474.84 ms;
- mean PDF size: 18,385 bytes.

The API correctness regression confirmed that PDF export uses the latest
immutable snapshot without re-reading S3 and safely maps renderer failures.

### Failure and retry behavior

Validated processing behavior includes:

- broker dispatch failure preserves the durable job for redrive;
- temporary storage failure transitions to retrying and then deferred;
- recovery reuses the same durable processing job;
- durable attempt limits stop further retries;
- malformed source data fails terminally without writing artifacts;
- Celery worker uses late acknowledgement and worker-loss rejection;
- live worker health remained available after throughput validation.

## Interpretation

Stage 8.9 passed because the measured workloads remained within the accepted
engineering targets while preserving correctness, tenant isolation,
idempotency, concurrency and retry invariants.

The next performance gate is staging validation. Staging should repeat these
measurements with deployment-equivalent PostgreSQL, Redis, worker counts,
network paths, object storage and representative customer reports before any
public latency or processing-time commitment is made.

---

<a id="8-milestone-specifications--contracts"></a>
# 8. Milestone Specifications & Contracts

## Milestone 5 Contract - Gemini AI and Atomic Credit Ledger

# Milestone 5 Contract - Gemini AI and Atomic Credit Ledger

## Scope

Milestone 5 adds tenant-scoped AI requests backed by Google Gemini and an auditable PostgreSQL credit ledger. The approved initial allowance is 15 full AI responses for the active trial or subscription period.

## Credit invariants

1. PostgreSQL is the source of truth for AI credit balances and ledger history.
2. A credit is reserved atomically before any provider request begins.
3. Reserved plus consumed credits may never exceed the effective entitlement limit.
4. Provider network calls must never run while a database row lock is held.
5. A reservation becomes consumed only after a valid full response is returned and durably persisted.
6. A reservation is released when the provider times out, fails, safety-blocks the request, or returns an unusable response.
7. The same tenant-scoped idempotency key may never create more than one ledger reservation.
8. Every account and ledger query must be tenant-scoped.
9. Trial usage and paid-period usage are separated by their authoritative subscription period boundaries.
10. Ledger history is append-auditable: entries transition from reserved to either consumed or released and never transition again.

## Stage boundaries

- Stage 1: credit account and ledger schema, migration, model registration, and contract tests.
- Stage 2: atomic reserve, consume, and release service with PostgreSQL concurrency tests.
- Stage 3: Gemini gateway abstraction, configuration, and test double.
- Stage 4: conversation/message persistence and tenant-scoped API.
- Stage 5: end-to-end provider execution, usage history, rate limiting, logging, and failure mapping.
## Stage 5 execution contract

1. A user message is durably persisted before credit reservation begins.
2. A recoverable database lease ensures only one active provider call owns an idempotent request.
3. Gemini is called only after all database transactions and row locks are closed.
4. A valid response, assistant message, user-message completion, and credit consumption are committed atomically.
5. Provider failures, safety blocks, timeouts, rejected requests, invalid responses, and oversized responses release the reservation.
6. Replaying a completed key returns the same assistant message without a second provider call.
7. Replaying an active request returns HTTP 202 until the current execution completes or its lease expires.
8. Replaying a failed key returns the same classified failure; a new attempt requires a new idempotency key.
9. Usage history exposes balances and ledger states without exposing raw idempotency digests or processing tokens.
10. Logs contain controlled identifiers and provider metadata, never raw prompts or financial rows.

## Milestone 7 Contract - Admin Operations

# Milestone 7 — Admin Operations

## Stage 1 — Safe read foundation

### Platform roles

Platform administration remains separate from business tenancy.

- `super_admin` is a platform-level privileged operator.
- `customer_service` is a platform-level support operator.
- `user` is a customer account.
- `owner` remains a tenant-scoped role and is not a platform administrator.

Platform operators must not receive tenant context through customer-facing
tenant dependencies.

### Stage 1 endpoints

- `GET /api/v1/admin/users`
- `GET /api/v1/admin/businesses`

Both endpoints require either:

- `super_admin`
- `customer_service`

### Customer Service restrictions

Customer Service may inspect basic customer account and tenant metadata required
for support.

Customer Service must not:

- enumerate other platform operator accounts;
- access uploaded financial files;
- access dashboard contents;
- access raw AI conversations or prompts;
- mutate subscription state;
- adjust AI credits;
- suspend accounts;
- impersonate users.

### Super Admin

Super Admin may enumerate platform roles as part of user administration.

High-risk mutation capabilities are intentionally deferred until the audit-event
foundation is present and permission-matrix tests prove that Customer Service
cannot perform Super Admin actions.

### Stage 1 security boundary

No Stage 1 endpoint performs customer financial-data retrieval or privileged
state mutation.

## Stage 2 — Administrative audit foundation

Privileged administrative mutations must be auditable before they are exposed.

Audit events are append-only and record:

- authenticated platform actor ID;
- actor role at the time of the action;
- normalized action and target type;
- immutable target UUID;
- optional business UUID;
- validated request ID;
- bounded, JSON-compatible safe metadata;
- creation timestamp.

Audit rows do not use cascading foreign keys because historical evidence must
survive later lifecycle changes to users, businesses, or targets.

The database rejects UPDATE and DELETE operations on audit rows.

The audit service flushes but does not commit. A privileged mutation and its
audit event must therefore share one transaction: both commit, or both roll
back.

Audit metadata must never contain credentials, passwords, OTPs, authorization
tokens, secrets, raw payloads, uploaded file contents, AI prompts, or message
contents.

No high-risk administrative mutation endpoint may be added until this audit
foundation passes unit, migration, and PostgreSQL integration validation.


## Stage 3 — Account suspension and reactivation

Account lifecycle mutations are Super Admin operations only.

The lifecycle contract is:

- only platform Super Admin may suspend or reactivate an account;
- Customer Service and customer users cannot perform these actions;
- platform operator accounts cannot be targets of the customer lifecycle API;
- suspension sets the customer user inactive;
- suspension revokes all unrevoked refresh sessions;
- suspension increments the user's authentication session generation;
- all previously issued access and refresh JWTs therefore remain invalid after
  later reactivation;
- reactivation never restores revoked sessions;
- a reactivated customer must authenticate again to obtain a new session;
- reason input is a closed operational reason code, not free-form sensitive text;
- every accepted lifecycle action writes an immutable audit event with the
  server-derived actor, target, business, request ID, state transition, and
  session-revocation count;
- lifecycle state, token revocation, session-generation change, and audit event
  commit atomically in one database transaction;
- audit failure rolls the entire lifecycle mutation back.

Migration 0014 introduces `users.auth_session_version` as the durable
authentication generation used to invalidate stateless access tokens safely.


## Stage 4 — Subscription and tenant operations

Stage 4 begins with read-only subscription visibility.

`GET /api/v1/admin/businesses/{business_id}/subscription` is available to
Super Admin and Customer Service for support diagnostics.

The response may expose:

- local subscription status;
- safe Plan identity and published pricing metadata;
- trial and current billing-period timestamps;
- scheduled cancellation state;
- whether the subscription is Stripe-managed;
- last successful Stripe synchronization timestamp;
- evaluated subscription access state;
- effective entitlement keys, limits, and sources.

The support response must not expose Stripe Customer IDs, Stripe Subscription
IDs, Stripe Price IDs, webhook identifiers, payment methods, card data, hosted
invoice URLs, invoice PDF URLs, or raw Stripe payloads.

Paid subscription lifecycle state remains Stripe-authoritative. Admin support
code must not directly edit Stripe-synchronized subscription fields.

Customer Service remains read-only for all subscription operations.

Any future Super Admin subscription mutation must reuse the Stripe gateway and
subscription synchronizer, write an immutable admin audit event, and must not
introduce an independent local source of subscription truth.


## Stage 5 — Controlled AI credit adjustments

The existing `ai_credit_ledger_entries` table remains the immutable reservation
state machine for one AI response: reserved, consumed, or released. Admin credit
adjustments must not overload or weaken those lifecycle invariants.

Administrative grants and reductions use the dedicated append-only
`ai_credit_adjustment_ledger_entries` ledger.

Adjustment rules:

- only the current subscription and access period may be adjusted;
- adjustments apply only to the `ai_full_responses` entitlement;
- the effective period limit is the entitlement limit plus the immutable sum of
  adjustment deltas for that credit account;
- `AICreditAccount.limit_value` is a materialized effective ceiling and is not
  an independent source of truth;
- negative adjustments must never reduce the effective limit below
  `reserved_count + consumed_count`;
- consequently an administrator cannot revoke credits already reserved by an
  in-flight AI execution or credits already consumed;
- unlimited entitlements cannot receive numeric credit adjustments;
- adjustment rows are append-only at the database layer;
- raw idempotency keys must never be persisted;
- every privileged adjustment must later be paired with an immutable admin audit
  event in the same transaction;
- Customer Service remains read-only and cannot create adjustments.

Migration `0015_m7_ai_credit_adjustments` introduces the adjustment ledger
without changing the existing reserved/consumed/released reservation statuses.


### Stage 5 privileged adjustment API

`POST /api/v1/admin/businesses/{business_id}/ai-credits/adjustments`
is a Super Admin-only mutation.

Each request requires:

- a non-zero signed `delta` between -1000 and 1000;
- a closed adjustment reason code;
- an `Idempotency-Key` header.

The API guarantees:

- Customer Service and customer users receive no mutation capability;
- the raw idempotency key is never stored;
- replaying the same key and payload returns the original adjustment;
- reusing the same key for a different adjustment is rejected;
- adjustments are serialized per business;
- no negative adjustment can reduce the effective limit below
  reserved plus consumed usage;
- positive and negative adjustments derive from the immutable adjustment
  ledger rather than arbitrary account counter edits;
- the materialized account limit is updated consistently with the
  immutable adjustment ledger;
- the adjustment ledger row and immutable admin audit event commit
  atomically;
- audit failure rolls back the adjustment and any newly created credit
  account;
- no adjustment modifies the reserved/consumed/released execution ledger.

The immutable admin audit action is `ai.credit.adjust`.

---

<a id="9-historical-milestone-implementation-reports"></a>
# 9. Historical Milestone Implementation Reports

## Milestone 0 — Phase 1 Revalidation Report

# Milestone 0 — Phase 1 Revalidation Report

**Project:** Spike Technology / Liza AI analytics SaaS backend  
**Revalidation date:** 2026-07-26  
**Baseline archive:** `spike_backend_phase1.zip`  
**Baseline SHA-256:** `33f51f1fe9aa2af4b7499a49bfb94ee5289013a241c420a33ad55721209e9b12`

## Result

**Pass. Milestone 0 is complete.**

The source, lockfile, linting, packaging, migration chain, authentication behavior,
PostgreSQL/Redis integration suite, Celery task delivery, and dependency audit all passed
locally. Two defects found during revalidation were corrected and covered by tests.

The repository's exact Docker Compose gate was subsequently run by the user on
2026-07-26. PostgreSQL 16 and Redis 7 were healthy, the API and worker remained running,
all 48 tests passed, both health checks returned `status: ok`, Celery inspection returned
`pong`, and the queued smoke task returned `status: ok`.

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
| Exact Docker Compose runtime | Pass | User-supplied Windows/Docker run completed every final gate |

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

## Docker-native validation commands

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

Milestone 0 required the Docker-native run to show:

- PostgreSQL and Redis healthy.
- API and worker running.
- One Alembic head and successful migration from the new empty database.
- 48 tests passing.
- Both health endpoints returning `{"status":"ok"}`.
- Celery inspection responding and the queued task returning `{"status":"ok", ...}`.

All conditions were met on 2026-07-26. Development may proceed from this verified
baseline without carrying an unverified Phase 1 foundation forward.

## Milestone 1 — Business and Subscription Foundation

# Milestone 1 — Business and Subscription Foundation

**Project:** Spike Technology / Liza AI analytics SaaS backend  
**Implementation date:** 2026-07-26  
**Baseline:** Fully passed Milestone 0 / Phase 1 release  
**Release version:** `0.2.0`  
**Alembic head:** `0004_m1_foundation`

## Result

**Implementation complete; exact Docker-native sign-off pending.**

The Milestone 1 source, migration, API contract, unit tests, and PostgreSQL-compatible
integration/security tests pass in the coding workspace. The final sign-off gate is the
same repository running through Docker Desktop with PostgreSQL 16 and Redis 7.

No AWS configuration is required. Local development remains:

```dotenv
EMAIL_BACKEND=console
AWS_REGION=
SES_FROM_EMAIL=
```

## Confirmed rules implemented

| Rule | Implementation |
| --- | --- |
| Business tenancy | One business per user |
| Tenant identity | Derived from the authenticated user's active role assignment; never accepted from a request body or header |
| Initial business role | `owner` |
| Trial | Standalone 14-day trial created during onboarding |
| Trial AI allowance | 15 full responses |
| Paid-plan AI allowance | 15 full responses per billing period |
| Premium | `$59.99/month`; 20 dashboards |
| Pro Plan | `$99.99/month` |
| Enterprise | Custom pricing |
| Unknown limits | Fail closed; no invented entitlement row |
| Stripe | Record fields are prepared, but no checkout or paid activation occurs in this milestone |

The authored “Save 30%” yearly promotion does not provide approved annual prices, so
annual billing values were not invented. Likewise, no unapproved dashboard limit was
assigned to Pro Plan, Enterprise, or the standalone trial.

## Data model

### `businesses`

The Phase 1 table remains the tenant root. Its unique `owner_user_id` preserves one
owned business per user.

### `role_assignments`

- Carries both `business_id` and `user_id`.
- `user_id` is unique, enforcing one tenant membership per v1 user.
- Only the approved `owner` role is accepted in v1.
- Inactive assignments cannot establish tenant context.
- Existing Phase 1 business rows are safely backfilled with their owner assignment.

### `plans` and `plan_entitlements`

The migration deterministically seeds:

| Code | Name | Monthly price | Confirmed entitlements |
| --- | --- | ---: | --- |
| `premium` | Premium | 5,999 cents USD | AI: 15; dashboards: 20 |
| `pro` | Pro Plan | 9,999 cents USD | AI: 15 |
| `enterprise` | Enterprise | Custom | AI: 15 |

Missing entitlement rows mean “not configured,” not “unlimited.”

### `subscriptions`

- Carries `business_id` on every record.
- Supports trial, active, Stripe failure/intermediate, and terminal states.
- Stores trial and billing-period boundaries.
- Preserves nullable Stripe customer/subscription identifiers for the next milestone.
- A PostgreSQL partial unique index rejects a second current subscription for the same
  business while allowing terminal history records.

### `subscription_entitlements`

- Carries `business_id` as a direct tenant boundary.
- Uses a composite `(subscription_id, business_id)` foreign key, so an entitlement
  cannot be attached to a subscription from another tenant.
- Supports trial, plan, and explicit-override sources.
- The onboarding transaction snapshots the confirmed 15-response trial allowance.

## API contract

| Method | Route | Access | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/plans` | Public | List active public plans and confirmed entitlements |
| `POST` | `/api/v1/businesses` | Authenticated regular user | Atomically create business, owner assignment, trial, and trial entitlement |
| `GET` | `/api/v1/businesses/me` | Active tenant owner | Return server-derived business context |
| `GET` | `/api/v1/subscriptions/me` | Active tenant owner | Return current tenant subscription |
| `GET` | `/api/v1/subscriptions/{subscription_id}` | Active tenant owner | Return the record only when it belongs to the current tenant |
| `GET` | `/api/v1/entitlements/me` | Active tenant owner | Return subscription access state and effective entitlements |

`BusinessCreate` contains only `name` and optional `industry`. It intentionally has no
`business_id`, `owner_user_id`, role, trial duration, plan, or entitlement fields.

## Tenant-isolation flow

1. The access JWT identifies the user.
2. The API loads that user's active `RoleAssignment`.
3. The assignment resolves the only allowed `Business`.
4. A `TenantScope` adds `business_id = current_business_id` to every tenant-record query.
5. Cross-tenant IDs return the same `404` response as unknown IDs.
6. Database foreign keys and unique indexes remain the final boundary if service code is
   bypassed or races.

Super Admin and Customer Service global roles do not receive an implicit tenant bypass.
Their separate, restricted operational endpoints remain a later milestone.

## Entitlement behavior

The entitlement service:

- rejects missing subscriptions;
- rejects expired trials and inactive paid states;
- requires configured billing periods for active paid subscriptions;
- merges plan entitlements with tenant-scoped subscription overrides;
- denies missing or disabled entitlements;
- requires callers to provide current usage explicitly;
- rejects use at or above a finite limit.

This milestone checks entitlements but does not claim atomic credit reservation. The
PostgreSQL `AIUsageLedger` and concurrent reservation/reversal transaction belong to the
Gemini workflow milestone.

## Validation evidence

| Check | Result |
| --- | --- |
| Ruff static analysis | Pass |
| Unit tests | 50 passed |
| Integration/security tests | 19 passed |
| Full suite | 69 passed |
| Alembic heads | One: `0004_m1_foundation` |
| Empty-database migration | Pass |
| Migration-to-SQLModel comparison | No schema differences |
| Offline upgrade SQL | Pass |
| Offline `0004` downgrade SQL | Pass |
| Concurrent onboarding | Exactly one `201`; competing request receives `409` |
| Cross-tenant subscription read | `404`, identical to unknown ID |
| Cross-tenant entitlement foreign key | Rejected by PostgreSQL |
| Second current subscription | Rejected by PostgreSQL |
| Expired trial | Access denied |
| Unknown dashboard entitlement | Denied, not treated as unlimited |

The local service substitute exercises asyncpg, SQLAlchemy, Alembic, and the application
against a PostgreSQL-compatible wire server. Exact PostgreSQL 16 / Redis 7 sign-off must
still be run through Docker Desktop.

## Docker-native sign-off

Use a fresh extraction of this release. If the Milestone 0 stack still occupies ports
5432, 6379, and 8000, stop it without deleting its volumes:

```powershell
docker compose -p spike_m0 stop
```

Then run:

```powershell
cd C:\Code\spike-technology-backend

uv python install 3.12
uv sync --python 3.12 --frozen --extra dev

docker compose -p spike_m1 up -d --build
docker compose -p spike_m1 ps

uv run alembic heads
uv run alembic upgrade head
uv run ruff check .

$env:RUN_INTEGRATION_TESTS="1"
$env:TEST_DATABASE_URL="postgresql+asyncpg://spike:spike_local_password@localhost:5432/spike_test"
uv run pytest -q

Invoke-RestMethod http://localhost:8000/api/v1/health/live
Invoke-RestMethod http://localhost:8000/api/v1/health/ready

docker compose -p spike_m1 exec worker celery -A app.workers.celery_app:celery_app inspect ping
docker compose -p spike_m1 exec api python -c "from app.workers.tasks import ping; print(ping.delay().get(timeout=10))"
```

Expected:

- `0004_m1_foundation (head)`
- Ruff: `All checks passed!`
- Pytest: `69 passed`
- PostgreSQL and Redis healthy
- API and worker running
- both health responses show `status: ok`
- Celery returns `pong`
- the queued task returns `status: ok`

The temporary `spike_m1` validation stack can be removed afterward without touching the
preserved Milestone 0 volumes:

```powershell
docker compose -p spike_m1 down -v
docker compose -p spike_m0 start
```

Only the explicitly named `spike_m1` validation volumes are deleted by that command.

## Completion rule

Milestone 1 is fully complete when the Docker-native gate above passes. After that,
development can safely proceed to Stripe Checkout, signed/idempotent webhooks, paid
subscription lifecycle, cancellation, renewal, and billing history.

## Milestone 2 — Stripe Billing

# Milestone 2 — Stripe Billing

**Project:** Spike Technology / Liza AI analytics SaaS backend  
**Implementation date:** 2026-07-26  
**Baseline:** Fully passed Milestone 1 release  
**Baseline archive SHA-256:** `1e1d9dcaa57a955a95c185ca1f7c75d58355c3441dc2f2e2305d2896385df862`  
**Release version:** `0.3.0`  
**Alembic head:** `0005_m2_stripe_billing`

## Result

**Implementation complete; exact Docker-native and Stripe-sandbox sign-off pending.**

All source, migration, API-contract, security, unit, and PostgreSQL-compatible
integration tests pass in the coding workspace. No Stripe secret or account was
available here, so this report does not claim that a real Stripe sandbox Checkout was
completed. Docker is also unavailable in this workspace; the exact PostgreSQL 16,
Redis 7, API, and worker container gate remains the final local-runtime check.

Stripe remains disabled by default. Existing authentication, onboarding, and
entitlement development continues without Stripe credentials.

## Confirmed billing policy

| Policy | Implemented behavior |
| --- | --- |
| Trial origin | Business onboarding starts the one approved 14-day trial |
| Checkout trial | Carries only the remaining onboarding trial; never grants another 14 days |
| Near-expiry trial | Checkout charges immediately when fewer than 49 hours remain because Stripe requires an absolute trial end at least 48 hours ahead |
| Self-service plans | Premium at USD 59.99/month and Pro Plan at USD 99.99/month |
| Enterprise | Sales-assisted; rejected by self-service Checkout |
| Paid activation | Only a signed Stripe subscription snapshot can attach a paid Plan |
| Cancellation | Scheduled at the current paid period end |
| Resume | Clears a scheduled end-of-period cancellation before Stripe ends the subscription |
| Immediate cancellation/refund | Intentionally absent until a refund/proration policy is approved |
| Inactive payment states | Fail closed; only `trialing` within its trial window and `active` within its current period grant access |
| Billing history | Tenant-scoped local mirror of Stripe invoice lifecycle events |

## API contract

| Method | Route | Access | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/billing/checkout-sessions` | Active tenant owner | Create or safely retry a Stripe Checkout Session |
| `POST` | `/api/v1/billing/subscription/cancel` | Active tenant owner | Set `cancel_at_period_end=true` |
| `POST` | `/api/v1/billing/subscription/resume` | Active tenant owner | Clear a scheduled cancellation |
| `GET` | `/api/v1/billing/history` | Active tenant owner | Read paginated local invoice history |
| `POST` | `/api/v1/billing/webhooks/stripe` | Signed Stripe request | Synchronize Checkout, subscription, invoice, and entitlement state |

The Checkout request accepts only `plan_code`. It cannot accept a business ID,
subscription ID, Stripe Customer, Stripe Price, trial boundary, amount, currency, or
entitlement.

Every Checkout request requires a printable 8–255 character `Idempotency-Key`.
Only its SHA-256 digest is persisted. A local operation receives a stable outbound
Stripe idempotency key, which makes a retry safe even if Stripe created the remote
Session before the original response was lost.

## Data model and migration

### Subscription mirror

`subscriptions` now also records:

- the authoritative Stripe Price ID;
- the latest applied Stripe Event ID/time;
- the last successful Stripe synchronization time.

### `billing_checkout_sessions`

- Carries `business_id` directly.
- Uses a composite `(subscription_id, business_id)` foreign key.
- Has one tenant-local idempotency record per client key digest.
- Has a PostgreSQL partial unique index allowing only one pending/open Checkout per
  business.
- Records a provisional expiry before the network request, preventing an interrupted
  request from locking Checkout indefinitely.
- Stores only the resulting Session ID, URL, expiry, Customer, and subscription
  references; no card data is stored.

### `billing_invoices`

- Carries `business_id` and a composite tenant/subscription foreign key.
- Mirrors Stripe invoice amount, currency, lifecycle status, period, hosted URL, and
  PDF URL.
- Uses a unique Stripe Invoice ID.
- Applies event-time and status ordering so a stale `draft`/`open` event cannot regress
  a paid or terminal invoice.
- Inserts concurrently delivered invoice events with a PostgreSQL conflict-safe path.

### `stripe_webhook_events`

- Uses Stripe Event ID as the unique idempotency boundary.
- Claims an Event transactionally before applying billing changes.
- Stores type, object ID, Stripe timestamp, live/test mode, and result.
- Does not store the raw webhook body.
- Rolls the claim back when processing fails, allowing Stripe's retry to process it.

The migration has a complete downgrade to `0004_m1_foundation`.

## Checkout and Price safety

Configuration validation rejects:

- live secret keys outside production;
- test secret keys in production;
- missing or malformed webhook secrets and Price IDs;
- a shared Price ID for Premium and Pro;
- non-HTTPS production callback URLs;
- a success URL without Stripe's `{CHECKOUT_SESSION_ID}` placeholder.

Before local Checkout creation, the service retrieves the configured Stripe Price and
requires all of the following:

- the exact configured Price ID;
- `active=true`;
- recurring rather than one-time billing;
- fixed `per_unit` billing;
- the exact approved local amount and currency;
- a one-month interval;
- licensed rather than metered usage.

This prevents a wrong dashboard Price mapping from silently charging a different amount
or granting the wrong Plan.

## Webhook and synchronization behavior

The endpoint verifies Stripe's signature using the exact raw request bytes and rejects
payloads over 1 MB. Test/live mode must match `APP_ENV`.

Handled Events:

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

Subscription and invoice handlers retrieve the current Stripe subscription snapshot
when appropriate, so webhook delivery order does not become the source of truth.
Current billing periods are read from the subscription item, matching the current
Stripe API shape.

For an app-managed subscription, metadata binds the Stripe object to the exact local
business, subscription, Plan, Checkout operation, and application environment. Unknown
Stripe Prices or statuses remove the local Plan and trial grant and set the subscription
to an inactive fail-closed state.

The browser success redirect never activates access.

## Entitlement synchronization

- While a Stripe subscription is `trialing`, the original trial AI allowance remains
  the override and paid-plan-only entitlements may come from the selected Plan.
- When Stripe reports a non-trial paid state, the standalone trial grant is deleted and
  effective entitlements come from the synchronized Plan.
- Renewal invoice/subscription events update the current item-level billing period.
- `active` access requires a configured period and the current time inside that period.
- `past_due`, `unpaid`, `paused`, `incomplete`, `incomplete_expired`, and `canceled`
  states do not grant access.
- An unmapped Price or unsupported status removes effective paid entitlements.

This milestone synchronizes entitlement availability. Atomic AI-credit reservation and
usage reversal remain part of the Gemini/usage-ledger milestone.

## Tenant and concurrency guarantees

- Business identity is derived only from the authenticated owner's active role
  assignment.
- Checkout, lifecycle, invoice-history, and entitlement queries carry the resolved
  `business_id`.
- Composite foreign keys reject cross-tenant Checkout and invoice records in
  PostgreSQL.
- Only one current subscription and one open Checkout can exist per business.
- Duplicate webhook delivery is claimed once even when requests arrive concurrently.
- Different lifecycle Events for the same invoice serialize without duplicate rows or
  stale status regression.
- Cancellation and resume operations lock the local subscription while synchronizing
  the requested Stripe state.

## Validation evidence

| Check | Result |
| --- | --- |
| Frozen lockfile | Pass |
| Python | 3.12 |
| Ruff lint | Pass |
| Ruff format | Pass |
| Unit tests | 59 passed |
| Integration/security tests | 22 passed |
| Full suite | 81 passed |
| Alembic heads | One: `0005_m2_stripe_billing` |
| Empty-database migration chain | Pass |
| Migration-to-SQLModel comparison | No schema differences |
| Offline upgrade SQL | Pass |
| Offline `0005` → `0004` downgrade SQL | Pass |
| OpenAPI creation | Pass; all five billing routes present |
| Package build | Pass; source distribution and wheel |
| Dependency vulnerability audit | Pass; no known vulnerabilities in the core/dev lock |
| Checkout idempotency/concurrency | Pass |
| Wrong Stripe Price configuration | Returns `503`; no Checkout created |
| Duplicate subscription webhook race | Exactly one Event claim |
| Concurrent invoice lifecycle race | One invoice row; latest paid state retained |
| Cross-tenant invoice foreign key | Rejected by PostgreSQL |
| Unknown Price/status | Paid access removed fail-closed |

The local database gate used a PostgreSQL-compatible wire server and an isolated Redis
protocol service to exercise asyncpg, SQLAlchemy, Alembic, transactional constraints,
and the complete application suite. It is not presented as a substitute for the exact
PostgreSQL 16 / Redis 7 Docker gate.

## Exact Docker-native sign-off

Use a fresh extraction of this release. If an older validation stack currently owns
ports 5432, 6379, or 8000, stop it without deleting its volumes:

```powershell
docker compose -p spike_m1 stop
docker compose -p spike_m0 stop
```

In the new Milestone 2 directory:

```powershell
cd C:\Code\spike-technology-backend

uv python install 3.12
uv sync --python 3.12 --frozen --extra dev

if (!(Test-Path .env)) {
    Copy-Item .env.example .env
}

docker compose -p spike_m2 up -d --build
docker compose -p spike_m2 ps

uv run alembic heads
uv run alembic upgrade head
uv run ruff check .
uv run ruff format --check .

$env:RUN_INTEGRATION_TESTS="1"
$env:TEST_DATABASE_URL="postgresql+asyncpg://spike:spike_local_password@localhost:5432/spike_test"
uv run pytest -q

Invoke-RestMethod http://localhost:8000/api/v1/health/live
Invoke-RestMethod http://localhost:8000/api/v1/health/ready

docker compose -p spike_m2 exec worker celery -A app.workers.celery_app:celery_app inspect ping
docker compose -p spike_m2 exec api python -c "from app.workers.tasks import ping; print(ping.delay().get(timeout=10))"
```

Expected:

- `0005_m2_stripe_billing (head)`
- Ruff lint and format checks pass
- `81 passed`
- PostgreSQL 16 and Redis 7 are healthy
- API and worker remain running
- both health responses contain `status: ok`
- Celery returns `pong`
- the queued task returns `status: ok`

## Stripe sandbox sign-off

1. In Stripe test mode, create active, licensed, fixed recurring monthly Prices:
   Premium USD 59.99 and Pro Plan USD 99.99.
2. Put the test secret and both Price IDs in `.env`.
3. Run:

   ```powershell
   stripe listen --forward-to http://localhost:8000/api/v1/billing/webhooks/stripe
   ```

4. Copy the CLI's current `whsec_...` value into `.env`, set
   `STRIPE_ENABLED=true`, and restart the API container.
5. Register and verify a fresh user, onboard one business, and call
   `POST /api/v1/billing/checkout-sessions` with a unique `Idempotency-Key`.
6. Open the returned Checkout URL and use Stripe's documented test payment method.
7. Confirm:
   - the Checkout webhook is acknowledged;
   - the subscription webhook binds the Stripe IDs to the correct tenant;
   - `/api/v1/entitlements/me` reflects the Stripe trial/paid state;
   - cancellation and resume change `cancel_at_period_end`;
   - invoice Events appear only in that tenant's `/api/v1/billing/history`;
   - duplicate Event delivery does not add a second history row.

Automated tests cover renewal-period advancement, payment failure, out-of-order
delivery, and concurrent delivery. Production keys must not be used for this gate.

## Completion rule

Milestone 2 is fully signed off when both:

1. the exact Docker-native gate reports 81 passing tests and healthy API/worker
   services; and
2. one real Stripe sandbox Checkout completes with signed webhooks and synchronized
   tenant entitlements.

Until then, the correct status is **code-complete, verification pending** rather than a
production billing launch.

## Milestone 3 — Secure Report Uploads

# Milestone 3 — Secure Report Uploads

| Field | Value |
| --- | --- |
| Project | Spike Technology / Liza AI analytics SaaS backend |
| Implementation date | 2026-07-28 |
| Baseline commit | `42797dd` |
| Baseline archive SHA-256 | `9e70bfee4b6305cd5a0d82ac0fe84f0681cd16433b966592b5863a52fde3b666` |
| Release version | `0.4.0` |
| Alembic head | `0007_m3_upload_positions` |

## Result

**Implementation complete; exact Docker-native and real AWS S3 sign-off pending.**

The source, migration, API contract, security checks, unit tests, and
PostgreSQL-compatible integration tests pass in the coding workspace. No AWS account
or upload bucket was available here, so this report does not claim that a real
presigned browser upload completed against AWS. Docker is also unavailable in this
workspace; PostgreSQL 16 and Redis 7 remain the final local-runtime gate.

S3 uploads remain disabled by default. Existing authentication, onboarding, billing,
and local development continue without AWS credentials.

## Delivered scope

| Requirement | Implemented behavior |
| --- | --- |
| Storage | Dedicated private AWS S3 bucket with fail-closed security preflight |
| Upload mechanism | Short-lived SigV4 presigned POST; application API never proxies the browser upload body |
| Formats | Canonical UTF-8 CSV, XLS, and XLSX only |
| Batch limit | One to five files |
| File limit | One byte through 25 MB per file |
| Ownership | Tenant and uploader derived from the authenticated active role assignment |
| Status | Batch and per-file pending/uploaded/rejected/expired state |
| Suspicious files | Filename, signature, compound-document, ZIP package, active-content, encryption, path, CRC, and expansion checks |
| Database | Tenant-bound foreign keys, size/type/status checks, and a complete Alembic upgrade/downgrade |
| Processing boundary | No row parsing, Celery report job, Gemini call, dashboard creation, or PDF work |

## API contract

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/report-uploads/batches` | Validate metadata, reserve tenant-owned records, and return signed S3 POST forms |
| `GET` | `/api/v1/report-uploads/batches/{batch_id}` | Read tenant-scoped upload status |
| `POST` | `/api/v1/report-uploads/batches/{batch_id}/complete` | Verify S3 objects and finalize statuses |

All routes require an authenticated active tenant owner. Cross-tenant and unknown batch
identifiers return the same `404` body.

The create request accepts only:

- `filename`
- `content_type`
- `size_bytes`

Business ID, user ID, bucket, prefix, key, status, and S3 metadata are server-owned.
Original filenames never become object keys.

## Presigned POST and S3 safety

Each generated POST policy fixes:

- the exact random server-owned object key;
- the exact canonical MIME type;
- an exact content-length range;
- `Cache-Control: no-store`;
- SSE-S3 with `AES256`;
- business, batch, upload, and expected-size metadata;
- a bounded one-to-sixty-minute lifetime.

The application refuses create and pending completion operations unless it can verify:

- all four Block Public Access switches;
- Bucket owner enforced object ownership;
- enabled versioning;
- a non-public bucket policy.

On completion, the API heads the current object and requires a real S3 version ID. It
then reads that exact version, preventing a later overwrite from changing the bytes
being validated. Size, MIME type, cache policy, encryption, signed metadata, ETag, and
upload time must still match. Rejected or late object versions are deleted.

The API response never exposes the bucket, object key, ETag, or version ID after the
initial signed form. There is no public upload or download route.

## File security validation

### All formats

- Unicode-normalized, path-free, non-hidden filenames only.
- Executable or active-content double extensions are rejected.
- Declared extension and canonical MIME type must agree.
- The declared and stored sizes must match exactly.

### CSV

- Must be non-empty UTF-8 or UTF-8 with BOM.
- NUL and disallowed control bytes are rejected.
- Common executable, archive, image, PDF, and Office binary signatures are rejected.

### XLS

- Must be a valid OLE compound document with a Workbook/Book stream.
- The stream must begin with a valid BIFF workbook BOF.
- Encrypted BIFF, VBA project records, Excel 4 macro sheets, VBA storages, and embedded
  object storages are rejected.

### XLSX

- Must contain the required workbook package parts and spreadsheet content type.
- Encrypted entries, unsafe paths, macros, ActiveX, embedded objects, external links,
  custom UI, corrupt members, excessive entry counts, expansion over 250 MB, and
  compression ratios over 100:1 are rejected.

This is bounded structural validation, not a claim of universal malware detection.
Files remain private and are never executed. Milestone 4 must preserve safe parser
configuration and treat all cell values as untrusted data.

## Data model and migration

### `report_upload_batches`

- Direct `business_id` ownership.
- Composite creator membership foreign key to
  `(role_assignments.business_id, role_assignments.user_id)`.
- One-to-five file count constraint.
- Pending/complete/partial/rejected/expired status.
- Database-enforced pending/terminal timestamp consistency.
- Unique `(id, business_id)` key for tenant-bound children.

### `report_uploads`

- Direct `business_id` and `batch_id`.
- Composite batch/tenant and uploader/tenant foreign keys.
- Canonical extension/MIME constraints.
- Expected and actual size constraints capped at 25 MB.
- Zero-based batch position with a per-batch uniqueness constraint.
- Unique, random server-owned S3 key.
- Version ID and ETag retained only for an accepted immutable object.
- Database-enforced pending/uploaded/rejected/expired field consistency.

The migration chain downgrades cleanly to `0005_m2_stripe_billing`.

## Tenant and concurrency guarantees

- Tenant identity comes only from the authenticated user's active owner assignment.
- Every batch and file query includes the resolved business ID.
- Composite foreign keys reject cross-tenant creator, uploader, and batch references.
- Completion locks the tenant-owned batch before external verification, serializing
  simultaneous completion attempts.
- Create, read, completion, and repeated-completion responses preserve request order.
- A terminal completion is idempotent.
- A missing object remains pending until expiry, then becomes expired.
- Database constraints remain the final guard if an application query is changed later.

## Validation evidence

| Check | Result |
| --- | --- |
| Frozen lockfile | Pass |
| Python | 3.12 |
| Ruff lint | Pass |
| Ruff format | Pass |
| Unit tests | 78 passed |
| Integration/security tests | 28 passed |
| Full suite | 106 passed |
| Alembic heads | One: `0007_m3_upload_positions` |
| Empty-database migration chain | Pass |
| Migration-to-SQLModel comparison | No schema differences |
| Presigned policy contract | Exact key/type/size/encryption/metadata asserted |
| Cross-tenant API access | Same `404` as an unknown ID |
| Cross-tenant creator foreign key | Rejected by the database |
| Suspicious object cleanup | Rejected exact object version deleted |

The local database gate uses a PostgreSQL-compatible wire server and an isolated Redis
protocol service to exercise asyncpg, SQLAlchemy, Alembic, transactional constraints,
and the application suite. It is not presented as a substitute for the exact
PostgreSQL 16 / Redis 7 Docker gate.

## Exact Docker-native sign-off

From the Milestone 3 branch:

```powershell
cd C:\Code\spike-technology-backend

uv python install 3.12
uv sync --python 3.12 --frozen --extra dev

if (!(Test-Path .env)) {
    Copy-Item .env.example .env
}

docker compose -p spike_m3 up -d --build
docker compose -p spike_m3 ps

uv run alembic heads
uv run alembic upgrade head
uv run ruff check .
uv run ruff format --check .

$env:RUN_INTEGRATION_TESTS="1"
$env:TEST_DATABASE_URL="postgresql+asyncpg://spike:spike_local_password@localhost:5432/spike_test"
uv run pytest -q

Invoke-RestMethod http://localhost:8000/api/v1/health/live
Invoke-RestMethod http://localhost:8000/api/v1/health/ready

docker compose -p spike_m3 exec worker celery -A app.workers.celery_app:celery_app inspect ping
docker compose -p spike_m3 exec api python -c "from app.workers.tasks import ping; print(ping.delay().get(timeout=10))"
```

Expected:

- `0007_m3_upload_positions (head)`
- Ruff lint and format checks pass
- `106 passed`
- PostgreSQL 16 and Redis 7 are healthy
- API and worker remain healthy
- Celery returns `pong`
- the queued task returns `status: ok`

## Real AWS S3 sign-off

1. Create a dedicated bucket with Block Public Access, Bucket owner enforced
   ownership, versioning, default encryption, and a non-public TLS-only policy.
2. Add exact frontend-origin CORS for `POST`.
3. Grant the application role only the bucket-inspection and prefix-scoped object
   actions documented in `README.md`.
4. Set `AWS_REGION`, `S3_UPLOAD_BUCKET`, and `S3_UPLOADS_ENABLED=true`, then restart the
   API.
5. Create a one-file CSV batch as an onboarded tenant owner.
6. Submit every returned form field plus the file directly to the returned S3 URL and
   require HTTP `201`.
7. Call the completion endpoint and require file `uploaded` and batch `complete`.
8. Confirm an anonymous S3 GET is denied.
9. Repeat with a renamed executable and confirm the record is rejected and its exact
   object version is removed.
10. Confirm a second tenant receives the same `404` response as a random batch ID.

## Completion rule

Milestone 3 is fully signed off when:

1. the exact Docker-native gate reports 106 passing tests and healthy services; and
2. the real AWS gate completes one valid browser upload and rejects one suspicious
   upload in the dedicated private bucket.

Until then, the correct status is **code-complete, environment verification pending**,
not a production upload launch.
