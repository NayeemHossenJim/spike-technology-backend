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
