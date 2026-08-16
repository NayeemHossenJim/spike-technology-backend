# Spike Technology Backend Operational Runbook

## Purpose

This runbook describes the first-response procedure for production operational
failures in the Spike Technology backend.

It is designed to work with the backend's structured JSON logs, request IDs,
health endpoints, Prometheus-compatible metrics, Celery worker health, and
durable report-processing state.

Do not place credentials, bearer tokens, customer payloads, uploaded report
contents, OTP values, JWTs, or provider secrets in incident notes.

## Health endpoints

### Liveness

`GET /api/v1/health/live`

A successful response proves that the API process can accept HTTP requests.

Liveness does not prove that PostgreSQL, Redis, Celery, S3, email, Stripe, or
Gemini are healthy.

### Readiness

`GET /api/v1/health/ready`

A successful response proves that the API can currently reach PostgreSQL and
Redis.

A `503` response means at least one required dependency is unavailable.

## Metrics access

Operational metrics are exposed at:

`GET /api/v1/health/metrics`

Production requires `METRICS_BEARER_TOKEN`.

Send the token using the standard Authorization header:

`Authorization: Bearer <metrics-token>`

Never place the metrics token in a URL, query parameter, source-control file,
log message, screenshot, or incident document.

The metrics endpoint intentionally returns `404` when authentication fails.

## Core metrics

### API

- `spike_http_requests_total`
- `spike_http_request_duration_seconds`
- `spike_unhandled_errors_total`

HTTP metrics use route templates rather than raw request URLs so that
user-provided paths and query values are not exported as metric labels.

### PostgreSQL

- `spike_postgresql_available`
- `spike_postgresql_probe_duration_seconds`

### Redis

- `spike_redis_available`
- `spike_redis_probe_duration_seconds`

### Report processing

- `spike_report_processing_metrics_available`
- `spike_report_processing_jobs`
- `spike_report_processing_failed_jobs`
- `spike_report_processing_retrying_jobs`
- `spike_report_processing_stale_leases`

## Structured logs

Production application logs are JSON.

Useful correlation fields include:

- `request_id`
- `route`
- `status_code`
- `duration_ms`
- `outcome`
- `error_code`
- `exception_type`
- `task_id`
- `job_id`
- `report_upload_id`
- `attempt_count`
- `phase`

For an API incident, begin with the affected request's `X-Request-ID` response
header and search structured logs for the same `request_id`.

Exception messages are intentionally not serialized by the application JSON
formatter because provider or user data may be sensitive.

## Alert response

Prometheus-compatible alert rules are stored in:

`ops/prometheus/alerts.yml`

The production monitoring platform must load these rules and route alert
notifications to the approved operational notification destination.

### SpikeUnhandledApplicationErrors

1. Confirm that `spike_unhandled_errors_total` increased.
2. Find `http_request_failed` events in structured API logs.
3. Correlate using `request_id`, route, and exception type.
4. Determine whether the error began after a release or configuration change.
5. Check PostgreSQL and Redis availability.
6. Escalate immediately if authentication, authorization, tenant isolation,
   billing, credit accounting, or data integrity may be affected.

### SpikeHighHttpServerErrorRate

1. Confirm the affected routes using HTTP metrics.
2. Check `http_request_completed` and `http_request_failed` logs.
3. Check PostgreSQL and Redis alerts.
4. Check worker health if affected routes depend on asynchronous processing.
5. If the condition began immediately after deployment, prepare rollback.

### SpikePostgresqlUnavailable

1. Confirm `/health/ready` is failing.
2. Check `spike_postgresql_available`.
3. Check database service health and network reachability.
4. Check recent credential, secret, DNS, firewall, or deployment changes.
5. Do not repeatedly run migrations while database state is uncertain.
6. Escalate before performing restore or destructive database operations.

### SpikeRedisUnavailable

1. Confirm `/health/ready` is failing.
2. Check `spike_redis_available`.
3. Check Redis service health and connectivity.
4. Check Celery broker and result-backend connectivity.
5. Check persistence and resource exhaustion before restarting Redis.

### SpikeProcessingMetricsUnavailable

1. Check PostgreSQL availability first.
2. Verify that the API can query report-processing tables.
3. Review metric-loader warning logs.
4. Treat missing processing metrics as unknown state, not as zero failures.

### SpikeStaleProcessingLeases

1. Check Celery worker health.
2. Check queued, processing, retrying, and failed job metrics.
3. Search worker logs using `job_id` and `task_id`.
4. Determine whether workers stopped, lost broker connectivity, exceeded
   execution time, or entered retry behavior.
5. Do not manually mutate durable job state without verifying retry and lease
   semantics.

## Celery worker checks

The production worker must respond successfully to the configured Celery ping
health check.

When a worker is unhealthy:

1. Check Redis first because it is the Celery broker.
2. Check worker process/container health.
3. Check recent task failures and retry logs.
4. Check stale processing leases.
5. Restart only after confirming that retry and lease behavior will not cause
   duplicate unsafe work.

## Sensitive-data handling

Never copy the following into alerts or incident documents:

- Authorization headers
- JWT access or refresh tokens
- OTP values
- password-reset secrets
- Stripe secrets or webhook secrets
- Gemini credentials
- AWS credentials
- metrics bearer tokens
- uploaded report contents
- raw customer query strings

Use IDs and safe structured metadata for correlation.

## Recovery and rollback

For a release-related incident:

1. Stop further production promotion.
2. Preserve logs and monitoring evidence.
3. Identify the last known-good release.
4. Determine whether database migrations are backward compatible.
5. Roll back application code only when doing so is safe for the current
   database schema.
6. Re-run liveness, readiness, metrics, worker, and smoke checks.
7. Confirm error rates return to normal before closing the incident.

Database restore and migration rollback require their dedicated production
procedures and must not be improvised during an incident.

## Incident closure

Before closing an operational incident:

1. Confirm liveness and readiness.
2. Confirm PostgreSQL and Redis availability metrics.
3. Confirm worker health.
4. Confirm no new unexpected unhandled errors.
5. Confirm stale processing leases have been resolved.
6. Record the affected release and root cause.
7. Record any corrective action or follow-up security/performance work.

Stage 8.9 will establish explicit performance and latency targets. Alert
thresholds based on those targets should be added only after those targets are
measured and approved.
