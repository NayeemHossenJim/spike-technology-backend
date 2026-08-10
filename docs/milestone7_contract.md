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
