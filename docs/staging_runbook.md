# Staging Deployment and Recovery Runbook

## Purpose

This runbook defines backend responsibilities for Milestone 8 Stage 8.11.

The backend team owns:

- staging backend deployment;
- API and worker deployment;
- PostgreSQL and Redis connectivity;
- migrations;
- backend smoke validation;
- backend release-blocker fixes;
- application rollback validation;
- PostgreSQL backup and restore validation.

The frontend team owns frontend implementation and frontend API integration.

Frontend E2E execution and UAT sign-off remain shared/external acceptance
gates and must not be marked complete until those teams confirm success.

## Frontend API handoff

The backend exposes:

- API prefix: `/api/v1`
- Swagger UI: `/docs`
- OpenAPI JSON: `/api/v1/openapi.json`
- liveness: `/api/v1/health/live`
- readiness: `/api/v1/health/ready`

The staging frontend origin must be configured in:

- `FRONTEND_URL`
- `CORS_ORIGINS`

The staging API hostname must be configured in:

- `TRUSTED_HOSTS`

## Staging configuration

Use `.env.staging.example` as the staging configuration checklist.

Staging uses:

`APP_ENV=staging`

Staging must use production-like infrastructure controls:

- HTTPS frontend URL;
- HTTPS Stripe redirect URLs;
- non-localhost PostgreSQL;
- non-localhost Redis;
- non-localhost Celery broker/result backend;
- explicit trusted hosts;
- no wildcard CORS;
- private S3 bucket;
- SES email delivery;
- Gemini enabled;
- metrics authentication;
- non-placeholder JWT credentials.

Stripe staging configuration is intentionally different from production.

Staging must use:

- Stripe test secret keys;
- Stripe test webhook secrets;
- Stripe test Price IDs.

Production must continue to use Stripe live mode.

Never reuse production secrets, production billing objects, production S3
buckets, or production customer data in staging.

## Staging deployment sequence

Use this order:

1. Confirm the intended Git commit passed CI.
2. Record the release Git commit.
3. Build an immutable backend image.
4. Record the image digest.
5. Provision staging PostgreSQL.
6. Provision staging Redis.
7. Provision the private staging S3 bucket.
8. Verify the SES staging sender.
9. Configure the Stripe test webhook.
10. Configure the staging Gemini credential.
11. Configure staging API DNS.
12. Configure staging TLS.
13. Configure the frontend staging origin.
14. Store staging secrets in the deployment platform secret manager.
15. Verify PostgreSQL connectivity.
16. Verify Redis connectivity.
17. Take a pre-deployment database backup.
18. Run `alembic upgrade head`.
19. Run `alembic current`.
20. Deploy the API.
21. Deploy the worker using the same image.
22. Verify liveness.
23. Verify readiness.
24. Verify Celery worker health.
25. Verify protected metrics.
26. Execute the backend smoke suite.
27. Provide the staging API URL and OpenAPI contract to the frontend team.

## Approved staging data

Only synthetic or explicitly approved test data may be used.

Do not copy production customer information into staging.

Do not copy:

- production user accounts;
- production uploaded reports;
- production authentication credentials;
- production billing records;
- production AI conversations;
- production admin audit records.

Staging accounts must be clearly identifiable as test accounts.

## PostgreSQL backup

Prefer the managed PostgreSQL provider's snapshot system when available.

For a portable logical backup, `pg_dump` may be used.

The application uses a SQLAlchemy URL such as:

`postgresql+asyncpg://...`

PostgreSQL command-line tools require a normal PostgreSQL connection URL.

Example operator procedure:

    export POSTGRES_BACKUP_URL='postgresql://USER:PASSWORD@HOST:5432/DATABASE'

    pg_dump       --format=custom       --no-owner       --no-privileges       --file=spike-staging.dump       "$POSTGRES_BACKUP_URL"

    unset POSTGRES_BACKUP_URL

Backups must be stored in encrypted, access-controlled storage.

Never commit a database dump.

## Database restore validation

Never restore a test backup over the active staging database.

Use a disposable restore database.

Example:

    export POSTGRES_RESTORE_URL='postgresql://USER:PASSWORD@HOST:5432/RESTORE_DATABASE'

    pg_restore       --exit-on-error       --no-owner       --no-privileges       --dbname="$POSTGRES_RESTORE_URL"       spike-staging.dump

    unset POSTGRES_RESTORE_URL

After restoration:

1. point a temporary backend validation process at the restored database;
2. run `alembic current`;
3. verify the expected migration revision;
4. run database-backed smoke checks;
5. verify representative tenant-owned records;
6. record the restore result;
7. destroy the disposable restore database.

A backup is not considered validated until restore validation succeeds.

## Application rollback

Prefer application-image rollback instead of immediately downgrading the
database.

Rollback test procedure:

1. record the current image digest;
2. record the current migration revision;
3. deploy the new staging release;
4. complete smoke validation;
5. redeploy the last known-good API image;
6. redeploy the last known-good worker image;
7. verify liveness;
8. verify readiness;
9. verify worker health;
10. run backend smoke validation;
11. redeploy the intended release;
12. repeat health validation.

## Migration rollback validation

All current Alembic revisions provide downgrade functions.

However, migration downgrade testing must use a disposable database restored
from staging backup data.

For a one-revision staging rollback test:

    alembic current
    alembic downgrade -1
    alembic current
    alembic upgrade head
    alembic current

After the downgrade/re-upgrade cycle, execute the relevant database tests and
verify data invariants.

Do not automatically run `alembic downgrade` against production during an
incident.

Database compatibility and backup state must be evaluated first.

## Backend smoke validation

At minimum verify:

1. `/api/v1/health/live` returns HTTP 200.
2. `/api/v1/health/ready` returns HTTP 200.
3. the Celery worker responds to `inspect ping`.
4. `/api/v1/openapi.json` is reachable.
5. `/docs` is reachable.
6. registration API flow works.
7. authentication API flow works.
8. business onboarding works.
9. S3 upload initiation works.
10. report processing completes successfully.
11. AI conversation execution works.
12. AI-credit consumption is correct.
13. dashboard generation works.
14. dashboard snapshot generation works.
15. PDF export works.
16. Stripe TEST Checkout creation works.
17. Stripe TEST webhook processing works.
18. SES staging email delivery works.
19. authenticated metrics access works.

Frontend browser E2E validation is a separate gate.

## Release blockers

The following block staging acceptance:

- migration failures;
- readiness failures;
- unavailable worker;
- PostgreSQL instability;
- Redis instability;
- failed tenant isolation;
- failed S3 upload processing;
- failed SES delivery;
- failed Stripe test webhook processing;
- failed Gemini processing;
- incorrect AI-credit accounting;
- failed dashboard generation;
- failed PDF generation;
- failed database restore validation;
- unsafe application rollback.

## Evidence to retain

Record:

- Git commit;
- image digest;
- migration revision;
- staging deployment timestamp;
- staging backend domain;
- liveness result;
- readiness result;
- worker health result;
- backend smoke result;
- backup identifier;
- restore-test result;
- rollback-test result;
- frontend E2E status;
- UAT status;
- unresolved release blockers.

Never record secret values in release evidence.
