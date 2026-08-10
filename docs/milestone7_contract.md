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
