from __future__ import annotations

import io
from uuid import uuid4

from sqlalchemy import ForeignKeyConstraint

from alembic import command
from app.models.processing import (
    DEFAULT_REPORT_PROCESSING_MAX_ATTEMPTS,
    ReportProcessingJob,
    ReportProcessingStatus,
)
from tests.unit.test_alembic import make_alembic_config


def test_processing_job_has_a_safe_durable_initial_state() -> None:
    job = ReportProcessingJob(
        business_id=uuid4(),
        report_upload_id=uuid4(),
        source_storage_version_id="source-version-1",
        source_etag="source-etag-1",
    )

    assert job.status is ReportProcessingStatus.QUEUED
    assert job.attempt_count == 0
    assert job.max_attempts == DEFAULT_REPORT_PROCESSING_MAX_ATTEMPTS
    assert job.dispatch_attempt_count == 0
    assert job.available_at is not None
    assert job.started_at is None
    assert job.lease_token is None
    assert job.finished_at is None
    assert job.error_code is None
    assert job.normalized_storage_key is None
    assert job.profile_storage_key is None


def test_processing_status_contract_is_closed() -> None:
    assert {status.value for status in ReportProcessingStatus} == {
        "queued",
        "processing",
        "completed",
        "failed",
    }


def test_processing_job_metadata_enforces_tenant_and_state_invariants() -> None:
    table = ReportProcessingJob.__table__
    constraint_names = {constraint.name for constraint in table.constraints}
    index_names = {index.name for index in table.indexes}

    assert {
        "fk_report_processing_jobs_upload_business",
        "uq_report_processing_jobs_report_upload",
        "uq_report_processing_jobs_id_business",
        "uq_report_processing_jobs_normalized_key",
        "uq_report_processing_jobs_profile_key",
        "ck_report_processing_jobs_status",
        "ck_report_processing_jobs_attempts",
        "ck_report_processing_jobs_dispatch",
        "ck_report_processing_jobs_status_fields",
        "ck_report_processing_jobs_output_fields",
        "ck_report_processing_jobs_source",
        "ck_report_processing_jobs_output_values",
        "ck_report_processing_jobs_timestamps",
    } <= constraint_names
    assert {
        "ix_report_processing_jobs_business_id",
        "ix_report_processing_jobs_dispatchable",
        "ix_report_processing_jobs_stale_lease",
    } <= index_names

    upload_tenant_fk = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_report_processing_jobs_upload_business"
    )
    assert [column.name for column in upload_tenant_fk.columns] == [
        "report_upload_id",
        "business_id",
    ]
    assert [element.target_fullname for element in upload_tenant_fk.elements] == [
        "report_uploads.id",
        "report_uploads.business_id",
    ]
    assert upload_tenant_fk.ondelete == "CASCADE"


def test_milestone4_migration_emits_the_processing_contract() -> None:
    output = io.StringIO()
    config = make_alembic_config()
    config.output_buffer = output

    command.upgrade(config, "0007_m3_upload_positions:head", sql=True)

    sql = output.getvalue()
    assert "CREATE TABLE report_processing_jobs" in sql
    assert "uq_report_uploads_id_business" in sql
    assert "fk_report_processing_jobs_upload_business" in sql
    assert "ck_report_processing_jobs_status_fields" in sql
    assert "ix_report_processing_jobs_dispatchable" in sql
