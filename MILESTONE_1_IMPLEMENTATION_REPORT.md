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
