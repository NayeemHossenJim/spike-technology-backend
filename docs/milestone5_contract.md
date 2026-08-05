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
