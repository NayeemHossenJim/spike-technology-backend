from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlmodel import Field

from app.models.base import TenantOwnedModel, TimestampedModel, UUIDPrimaryKey

MAX_REPORT_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_REPORT_UPLOAD_FILES_PER_BATCH = 5


class ReportUploadBatchStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    PARTIAL = "partial"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ReportUploadStatus(StrEnum):
    PENDING = "pending"
    UPLOADED = "uploaded"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ReportUploadBatch(
    TenantOwnedModel,
    UUIDPrimaryKey,
    TimestampedModel,
    table=True,
):
    __tablename__ = "report_upload_batches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "created_by_user_id"],
            ["role_assignments.business_id", "role_assignments.user_id"],
            ondelete="CASCADE",
            name="fk_report_upload_batches_business_creator",
        ),
        UniqueConstraint(
            "id",
            "business_id",
            name="uq_report_upload_batches_id_business",
        ),
        CheckConstraint(
            "status IN ('pending', 'complete', 'partial', 'rejected', 'expired')",
            name="ck_report_upload_batches_status",
        ),
        CheckConstraint(
            f"file_count BETWEEN 1 AND {MAX_REPORT_UPLOAD_FILES_PER_BATCH}",
            name="ck_report_upload_batches_file_count",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_report_upload_batches_expiry",
        ),
        CheckConstraint(
            "(status = 'pending' AND completed_at IS NULL) OR "
            "(status <> 'pending' AND completed_at IS NOT NULL)",
            name="ck_report_upload_batches_status_fields",
        ),
    )

    created_by_user_id: UUID = Field(nullable=False, index=True)
    status: ReportUploadBatchStatus = Field(
        default=ReportUploadBatchStatus.PENDING,
        sa_column=Column(
            String(32),
            nullable=False,
            default=ReportUploadBatchStatus.PENDING.value,
        ),
    )
    file_count: int = Field(sa_column=Column(Integer, nullable=False))
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class ReportUpload(
    TenantOwnedModel,
    UUIDPrimaryKey,
    TimestampedModel,
    table=True,
):
    __tablename__ = "report_uploads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id", "business_id"],
            ["report_upload_batches.id", "report_upload_batches.business_id"],
            ondelete="CASCADE",
            name="fk_report_uploads_batch_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "uploaded_by_user_id"],
            ["role_assignments.business_id", "role_assignments.user_id"],
            ondelete="CASCADE",
            name="fk_report_uploads_business_uploader",
        ),
        UniqueConstraint(
            "batch_id",
            "batch_position",
            name="uq_report_uploads_batch_position",
        ),
        CheckConstraint(
            f"batch_position BETWEEN 0 AND {MAX_REPORT_UPLOAD_FILES_PER_BATCH - 1}",
            name="ck_report_uploads_batch_position",
        ),
        CheckConstraint(
            "status IN ('pending', 'uploaded', 'rejected', 'expired')",
            name="ck_report_uploads_status",
        ),
        CheckConstraint(
            "file_extension IN ('csv', 'xls', 'xlsx')",
            name="ck_report_uploads_extension",
        ),
        CheckConstraint(
            f"expected_size_bytes BETWEEN 1 AND {MAX_REPORT_UPLOAD_BYTES}",
            name="ck_report_uploads_expected_size",
        ),
        CheckConstraint(
            "actual_size_bytes IS NULL "
            f"OR actual_size_bytes BETWEEN 1 AND {MAX_REPORT_UPLOAD_BYTES}",
            name="ck_report_uploads_actual_size",
        ),
        CheckConstraint(
            "(file_extension = 'csv' AND content_type = 'text/csv') OR "
            "(file_extension = 'xls' AND content_type = 'application/vnd.ms-excel') OR "
            "(file_extension = 'xlsx' AND content_type = "
            "'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')",
            name="ck_report_uploads_content_type",
        ),
        CheckConstraint(
            "char_length(storage_bucket) > 0 AND char_length(storage_key) > 0",
            name="ck_report_uploads_storage_location",
        ),
        CheckConstraint(
            "("
            "status = 'pending' "
            "AND actual_size_bytes IS NULL "
            "AND storage_version_id IS NULL "
            "AND etag IS NULL "
            "AND uploaded_at IS NULL "
            "AND rejected_at IS NULL "
            "AND rejection_code IS NULL"
            ") OR ("
            "status = 'uploaded' "
            "AND actual_size_bytes IS NOT NULL "
            "AND storage_version_id IS NOT NULL "
            "AND etag IS NOT NULL "
            "AND uploaded_at IS NOT NULL "
            "AND rejected_at IS NULL "
            "AND rejection_code IS NULL"
            ") OR ("
            "status IN ('rejected', 'expired') "
            "AND actual_size_bytes IS NULL "
            "AND storage_version_id IS NULL "
            "AND etag IS NULL "
            "AND uploaded_at IS NULL "
            "AND rejected_at IS NOT NULL "
            "AND rejection_code IS NOT NULL"
            ")",
            name="ck_report_uploads_status_fields",
        ),
    )

    batch_id: UUID = Field(nullable=False, index=True)
    batch_position: int = Field(sa_column=Column(Integer, nullable=False))
    uploaded_by_user_id: UUID = Field(nullable=False, index=True)
    original_filename: str = Field(
        sa_column=Column(String(255), nullable=False),
    )
    file_extension: str = Field(
        sa_column=Column(String(8), nullable=False),
    )
    content_type: str = Field(
        sa_column=Column(String(255), nullable=False),
    )
    expected_size_bytes: int = Field(
        sa_column=Column(Integer, nullable=False),
    )
    storage_bucket: str = Field(
        sa_column=Column(String(63), nullable=False),
    )
    storage_key: str = Field(
        sa_column=Column(String(1024), nullable=False, unique=True),
    )
    status: ReportUploadStatus = Field(
        default=ReportUploadStatus.PENDING,
        sa_column=Column(
            String(32),
            nullable=False,
            default=ReportUploadStatus.PENDING.value,
        ),
    )
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
    actual_size_bytes: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
    )
    storage_version_id: str | None = Field(
        default=None,
        sa_column=Column(String(1024), nullable=True),
    )
    etag: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )
    uploaded_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    rejected_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    rejection_code: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
