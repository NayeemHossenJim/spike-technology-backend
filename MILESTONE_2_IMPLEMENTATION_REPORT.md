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
