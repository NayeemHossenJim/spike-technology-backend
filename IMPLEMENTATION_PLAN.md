# Spike Technology Backend - Implementation Plan

**Last updated:** 2026-08-11

## 1. Project status

The backend implementation through **Milestone 7** is complete.

Current validated checkpoint:

| Item | Value |
|---|---|
| Branch | `develop` |
| Milestone 7 merge commit | `2c5ee900ba987393f4aaa4986b780798962079be` |
| Alembic head | `0015_m7_ai_credit_adjustments` |
| Full test suite | **283 passed** |
| Full integration suite | **85 passed** |
| Milestone 7 integration suite | **22 passed** |

The complete migration chain has also been proven against a fresh PostgreSQL
database from the initial migration through `0015`.

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

### Milestone 7 release validation

Validated:

- M7 integration: **22 passed**;
- complete integration: **85 passed**;
- complete project: **283 passed**;
- migration upgrade: empty DB -> `0015`;
- migration downgrade: `0015` -> `0012`;
- migration re-upgrade: `0012` -> `0015`;
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

- [ ] dependency vulnerability scan;
- [ ] authentication regression;
- [ ] authorization regression;
- [ ] tenant-isolation regression;
- [ ] admin permission-matrix regression;
- [ ] JWT/session-revocation regression;
- [ ] Stripe webhook replay tests;
- [ ] upload-abuse tests;
- [ ] rate-limit tests;
- [ ] sensitive-response inspection;
- [ ] sensitive-log inspection;
- [ ] AI-credit concurrency tests;
- [ ] audit immutability tests;
- [ ] secrets scan.

### Exit criteria

No known high-risk authorization, tenancy, credential or administrative
security defect remains.

---

## Stage 8.9 - Performance and Reliability

### Work

- [ ] API load testing;
- [ ] authentication load testing;
- [ ] upload load testing;
- [ ] database concurrency testing;
- [ ] AI-credit concurrency testing;
- [ ] Celery throughput testing;
- [ ] large-report processing testing;
- [ ] dashboard-generation testing;
- [ ] PDF-generation testing;
- [ ] failure/retry behavior validation;
- [ ] establish acceptable latency targets.

### Exit criteria

The backend meets agreed performance targets without correctness failures.

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

---

## Stage 8.11 - Staging and UAT

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
- [ ] performance validation passes;
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
