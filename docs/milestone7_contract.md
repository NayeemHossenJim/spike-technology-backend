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