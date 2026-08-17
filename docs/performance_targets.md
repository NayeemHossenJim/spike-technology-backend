# Performance and Reliability Baselines

## Purpose

This document records the engineering baselines established during
Milestone 8 Stage 8.9.

The figures below are release-validation targets for the tested local
Docker/PostgreSQL/Redis environment. They are not public customer SLAs and
must be revalidated in staging and production using representative traffic,
infrastructure sizing and customer report files.

Client-to-S3 object-transfer time and third-party provider latency are not
included in API command-acceptance targets.

## Accepted engineering targets

| Workload | Stage 8.9 engineering target |
|---|---|
| Normal authenticated API reads | p95 <= 500 ms at the validated baseline load |
| Authentication | preserve correctness under the validated concurrent authentication load; password-hash-backed login is assessed separately from normal read latency |
| Upload API command acceptance | p95 <= 500 ms, excluding direct client-to-object-storage transfer |
| Database concurrency | zero lost updates, invalid state transitions or pool correctness failures under validated concurrency |
| AI-credit reserve | p95 <= 350 ms under the validated 8-operation concurrency workload |
| AI-credit release | p95 <= 200 ms under the validated 8-operation concurrency workload |
| Celery lightweight-task throughput | >= 500 completed tasks/s for the validated 240-task workload, p95 <= 250 ms |
| 50,000-row CSV processing | <= 1.0 s locally and >= 50,000 rows/s |
| 10,000-record dashboard snapshot | create and identical replay each <= 250 ms locally |
| Dashboard PDF rendering | p95 <= 750 ms after runtime initialization |
| Failure/retry behavior | no duplicate terminal effects; retries preserve durable job identity and bounded state transitions |

## Validation evidence

### API, authentication, upload and database

Stage 8.9A through Stage 8.9C validated API load, authentication load,
upload behavior and database concurrency against the isolated local
PostgreSQL/Redis test environment.

Those runs completed without correctness failures.

Their exact terminal percentiles were not persisted in the repository at
Stage 8.9 closure, so this document intentionally does not reconstruct or
invent numeric results that are no longer authoritative.

The general API latency targets above therefore remain the approved
engineering baselines for subsequent staging validation.

### AI-credit concurrency

Existing concurrency contracts passed for:

- preventing credit overspend;
- same-idempotency-key serialization;
- mutually exclusive terminal consume/release transitions.

Measured temporary benchmark:

- reserve workload: 60 operations, concurrency 8;
- reserve throughput: 88.44 operations/s;
- reserve p50: 56.72 ms;
- reserve p95: 272.40 ms;
- reserve p99: 300.88 ms;
- release workload: 60 operations, concurrency 8;
- release throughput: 201.91 operations/s;
- release p50: 26.70 ms;
- release p95: 114.48 ms;
- release p99: 122.11 ms.

### Celery throughput

The authoritative Celery benchmark was executed from the API container so
the producer, Redis broker/result backend and worker shared the intended
Docker network path.

Validated workload:

- tasks submitted: 240;
- tasks completed: 240;
- failures: 0;
- enqueue rate: 1,417.42 tasks/s;
- completion rate: 787.70 tasks/s;
- p50 completion latency: 112.07 ms;
- p95 completion latency: 129.47 ms;
- p99 completion latency: 136.28 ms;
- queue depth after completion: 0.

An earlier host-side attempt was excluded because the Windows producer was
connected to a separate Redis instance from the Docker worker. That
environment mismatch was diagnosed and the stale benchmark queue was
removed without affecting Docker Redis.

### Large-report processing

Validated CSV parser ceiling:

- maximum rows per sheet: 100,000.

Representative benchmark:

- source rows: 50,000;
- source bytes: 1,391,423;
- normalized artifact bytes: 602,821;
- profile artifact bytes: 1,233;
- processing time: 0.480 s;
- processing throughput: 104,124.97 rows/s;
- final durable processing state: completed.

### Dashboard generation

Representative immutable snapshot benchmark:

- sources: 2;
- rows per source: 5,000;
- total records: 10,000;
- materialization time: 0.104 s;
- identical replay time: 0.099 s;
- source artifact reads: 8.

Dashboard concurrency regression also confirmed that concurrent creates
cannot exceed the configured entitlement limit and identical concurrent
snapshot requests persist only one logical snapshot.

### PDF generation

Runtime:

- WeasyPrint 69.0;
- Linux API container.

Representative benchmark:

- iterations: 8;
- warm-up: 499.83 ms;
- p50: 490.04 ms;
- p95: 515.38 ms;
- p99: 515.38 ms;
- maximum: 515.38 ms;
- mean: 474.84 ms;
- mean PDF size: 18,385 bytes.

The API correctness regression confirmed that PDF export uses the latest
immutable snapshot without re-reading S3 and safely maps renderer failures.

### Failure and retry behavior

Validated processing behavior includes:

- broker dispatch failure preserves the durable job for redrive;
- temporary storage failure transitions to retrying and then deferred;
- recovery reuses the same durable processing job;
- durable attempt limits stop further retries;
- malformed source data fails terminally without writing artifacts;
- Celery worker uses late acknowledgement and worker-loss rejection;
- live worker health remained available after throughput validation.

## Interpretation

Stage 8.9 passed because the measured workloads remained within the accepted
engineering targets while preserving correctness, tenant isolation,
idempotency, concurrency and retry invariants.

The next performance gate is staging validation. Staging should repeat these
measurements with deployment-equivalent PostgreSQL, Redis, worker counts,
network paths, object storage and representative customer reports before any
public latency or processing-time commitment is made.
