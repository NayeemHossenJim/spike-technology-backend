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

## Stage 2 ? Administrative audit foundation

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


## Stage 3 ? Account suspension and reactivation

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


## Stage 4 ? Subscription and tenant operations

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


## Stage 5 ? Controlled AI credit adjustments

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
