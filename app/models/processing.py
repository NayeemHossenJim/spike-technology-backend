from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlmodel import Field

from app.models.base import TenantOwnedModel, TimestampedModel, UUIDPrimaryKey, utc_now

DEFAULT_REPORT_PROCESSING_MAX_ATTEMPTS = 3
MAX_REPORT_PROCESSING_ATTEMPTS = 10


class ReportProcessingStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ReportProcessingJob(
    TenantOwnedModel,
    UUIDPrimaryKey,
    TimestampedModel,
    table=True,
):
    """Durable, tenant-bound processing state for one verified report upload."""

    __tablename__ = "report_processing_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["report_upload_id", "business_id"],
            ["report_uploads.id", "report_uploads.business_id"],
            ondelete="CASCADE",
            name="fk_report_processing_jobs_upload_business",
        ),
        UniqueConstraint(
            "report_upload_id",
            name="uq_report_processing_jobs_report_upload",
        ),
        UniqueConstraint(
            "id",
            "business_id",
            name="uq_report_processing_jobs_id_business",
        ),
        UniqueConstraint(
            "normalized_storage_key",
            name="uq_report_processing_jobs_normalized_key",
        ),
        UniqueConstraint(
            "profile_storage_key",
            name="uq_report_processing_jobs_profile_key",
        ),
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_report_processing_jobs_status",
        ),
        CheckConstraint(
            f"max_attempts BETWEEN 1 AND {MAX_REPORT_PROCESSING_ATTEMPTS} "
            "AND attempt_count BETWEEN 0 AND max_attempts "
            "AND ((attempt_count = 0 AND started_at IS NULL AND error_code IS NULL) "
            "OR (attempt_count > 0 AND started_at IS NOT NULL))",
            name="ck_report_processing_jobs_attempts",
        ),
        CheckConstraint(
            "dispatch_attempt_count >= 0 AND ("
            "(dispatch_attempt_count = 0 AND last_dispatched_at IS NULL) OR "
            "(dispatch_attempt_count > 0 AND last_dispatched_at IS NOT NULL)"
            ")",
            name="ck_report_processing_jobs_dispatch",
        ),
        CheckConstraint(
            "(status = 'queued' "
            "AND available_at IS NOT NULL "
            "AND lease_token IS NULL "
            "AND lease_expires_at IS NULL "
            "AND finished_at IS NULL) OR "
            "(status = 'processing' "
            "AND available_at IS NULL "
            "AND attempt_count > 0 "
            "AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL "
            "AND error_code IS NULL "
            "AND finished_at IS NULL) OR "
            "(status = 'completed' "
            "AND available_at IS NULL "
            "AND attempt_count > 0 "
            "AND lease_token IS NULL "
            "AND lease_expires_at IS NULL "
            "AND error_code IS NULL "
            "AND finished_at IS NOT NULL) OR "
            "(status = 'failed' "
            "AND available_at IS NULL "
            "AND attempt_count > 0 "
            "AND lease_token IS NULL "
            "AND lease_expires_at IS NULL "
            "AND error_code IS NOT NULL "
            "AND finished_at IS NOT NULL)",
            name="ck_report_processing_jobs_status_fields",
        ),
        CheckConstraint(
            "(status = 'completed' "
            "AND result_storage_bucket IS NOT NULL "
            "AND normalized_storage_key IS NOT NULL "
            "AND normalized_storage_version_id IS NOT NULL "
            "AND normalized_etag IS NOT NULL "
            "AND normalized_size_bytes IS NOT NULL "
            "AND profile_storage_key IS NOT NULL "
            "AND profile_storage_version_id IS NOT NULL "
            "AND profile_etag IS NOT NULL "
            "AND record_count IS NOT NULL "
            "AND sheet_count IS NOT NULL) OR "
            "(status <> 'completed' "
            "AND result_storage_bucket IS NULL "
            "AND normalized_storage_key IS NULL "
            "AND normalized_storage_version_id IS NULL "
            "AND normalized_etag IS NULL "
            "AND normalized_size_bytes IS NULL "
            "AND profile_storage_key IS NULL "
            "AND profile_storage_version_id IS NULL "
            "AND profile_etag IS NULL "
            "AND record_count IS NULL "
            "AND sheet_count IS NULL)",
            name="ck_report_processing_jobs_output_fields",
        ),
        CheckConstraint(
            "char_length(source_storage_version_id) > 0 AND char_length(source_etag) > 0",
            name="ck_report_processing_jobs_source",
        ),
        CheckConstraint(
            "(result_storage_bucket IS NULL OR "
            "char_length(result_storage_bucket) BETWEEN 3 AND 63) "
            "AND (normalized_storage_key IS NULL OR "
            "char_length(normalized_storage_key) > 0) "
            "AND (normalized_storage_version_id IS NULL OR "
            "char_length(normalized_storage_version_id) > 0) "
            "AND (normalized_etag IS NULL OR char_length(normalized_etag) > 0) "
            "AND (profile_storage_key IS NULL OR char_length(profile_storage_key) > 0) "
            "AND (profile_storage_version_id IS NULL OR "
            "char_length(profile_storage_version_id) > 0) "
            "AND (profile_etag IS NULL OR char_length(profile_etag) > 0) "
            "AND (normalized_size_bytes IS NULL OR normalized_size_bytes > 0) "
            "AND (record_count IS NULL OR record_count >= 0) "
            "AND (sheet_count IS NULL OR sheet_count > 0)",
            name="ck_report_processing_jobs_output_values",
        ),
        CheckConstraint(
            "(available_at IS NULL OR available_at >= created_at) "
            "AND (last_dispatched_at IS NULL OR last_dispatched_at >= created_at) "
            "AND (started_at IS NULL OR started_at >= created_at) "
            "AND (finished_at IS NULL OR finished_at >= started_at) "
            "AND (lease_expires_at IS NULL OR lease_expires_at > started_at)",
            name="ck_report_processing_jobs_timestamps",
        ),
        Index(
            "ix_report_processing_jobs_dispatchable",
            "available_at",
            "last_dispatched_at",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "ix_report_processing_jobs_stale_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'processing'"),
        ),
    )

    report_upload_id: UUID = Field(nullable=False)
    source_storage_version_id: str = Field(
        sa_column=Column(String(1024), nullable=False),
    )
    source_etag: str = Field(
        sa_column=Column(String(255), nullable=False),
    )
    status: ReportProcessingStatus = Field(
        default=ReportProcessingStatus.QUEUED,
        sa_column=Column(
            String(32),
            nullable=False,
            default=ReportProcessingStatus.QUEUED.value,
        ),
    )
    attempt_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, default=0),
    )
    max_attempts: int = Field(
        default=DEFAULT_REPORT_PROCESSING_MAX_ATTEMPTS,
        sa_column=Column(
            Integer,
            nullable=False,
            default=DEFAULT_REPORT_PROCESSING_MAX_ATTEMPTS,
        ),
    )
    dispatch_attempt_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, default=0),
    )
    available_at: datetime | None = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_dispatched_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    lease_token: UUID | None = Field(default=None, nullable=True)
    lease_expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    finished_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    error_code: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
    result_storage_bucket: str | None = Field(
        default=None,
        sa_column=Column(String(63), nullable=True),
    )
    normalized_storage_key: str | None = Field(
        default=None,
        sa_column=Column(String(1024), nullable=True),
    )
    normalized_storage_version_id: str | None = Field(
        default=None,
        sa_column=Column(String(1024), nullable=True),
    )
    normalized_etag: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )
    normalized_size_bytes: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    profile_storage_key: str | None = Field(
        default=None,
        sa_column=Column(String(1024), nullable=True),
    )
    profile_storage_version_id: str | None = Field(
        default=None,
        sa_column=Column(String(1024), nullable=True),
    )
    profile_etag: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )
    record_count: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    sheet_count: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
    )
