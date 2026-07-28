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
