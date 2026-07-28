"""add private tenant-owned report upload batches and files

Revision ID: 0006_m3_secure_uploads
Revises: 0005_m2_stripe_billing
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0006_m3_secure_uploads"
down_revision: str | Sequence[str] | None = "0005_m2_stripe_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_upload_batches",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'complete', 'partial', 'rejected', 'expired')",
            name="ck_report_upload_batches_status",
        ),
        sa.CheckConstraint(
            "file_count BETWEEN 1 AND 5",
            name="ck_report_upload_batches_file_count",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_report_upload_batches_expiry",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND completed_at IS NULL) OR "
            "(status <> 'pending' AND completed_at IS NOT NULL)",
            name="ck_report_upload_batches_status_fields",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_id", "created_by_user_id"],
            ["role_assignments.business_id", "role_assignments.user_id"],
            ondelete="CASCADE",
            name="fk_report_upload_batches_business_creator",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "business_id",
            name="uq_report_upload_batches_id_business",
        ),
    )
    op.create_index(
        "ix_report_upload_batches_business_id",
        "report_upload_batches",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        "ix_report_upload_batches_created_by_user_id",
        "report_upload_batches",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_report_upload_batches_expires_at",
        "report_upload_batches",
        ["expires_at"],
        unique=False,
    )

    op.create_table(
        "report_uploads",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_extension", sa.String(length=8), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("expected_size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_bucket", sa.String(length=63), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actual_size_bytes", sa.Integer(), nullable=True),
        sa.Column("storage_version_id", sa.String(length=1024), nullable=True),
        sa.Column("etag", sa.String(length=255), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'uploaded', 'rejected', 'expired')",
            name="ck_report_uploads_status",
        ),
        sa.CheckConstraint(
            "file_extension IN ('csv', 'xls', 'xlsx')",
            name="ck_report_uploads_extension",
        ),
        sa.CheckConstraint(
            "expected_size_bytes BETWEEN 1 AND 26214400",
            name="ck_report_uploads_expected_size",
        ),
        sa.CheckConstraint(
            "actual_size_bytes IS NULL OR actual_size_bytes BETWEEN 1 AND 26214400",
            name="ck_report_uploads_actual_size",
        ),
        sa.CheckConstraint(
            "(file_extension = 'csv' AND content_type = 'text/csv') OR "
            "(file_extension = 'xls' AND content_type = 'application/vnd.ms-excel') OR "
            "(file_extension = 'xlsx' AND content_type = "
            "'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')",
            name="ck_report_uploads_content_type",
        ),
        sa.CheckConstraint(
            "char_length(storage_bucket) > 0 AND char_length(storage_key) > 0",
            name="ck_report_uploads_storage_location",
        ),
        sa.CheckConstraint(
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
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "business_id"],
            ["report_upload_batches.id", "report_upload_batches.business_id"],
            ondelete="CASCADE",
            name="fk_report_uploads_batch_business",
        ),
        sa.ForeignKeyConstraint(
            ["business_id", "uploaded_by_user_id"],
            ["role_assignments.business_id", "role_assignments.user_id"],
            ondelete="CASCADE",
            name="fk_report_uploads_business_uploader",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(
        "ix_report_uploads_batch_id",
        "report_uploads",
        ["batch_id"],
        unique=False,
    )
    op.create_index(
        "ix_report_uploads_business_id",
        "report_uploads",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        "ix_report_uploads_expires_at",
        "report_uploads",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_report_uploads_uploaded_by_user_id",
        "report_uploads",
        ["uploaded_by_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_report_uploads_uploaded_by_user_id",
        table_name="report_uploads",
    )
    op.drop_index("ix_report_uploads_expires_at", table_name="report_uploads")
    op.drop_index("ix_report_uploads_business_id", table_name="report_uploads")
    op.drop_index("ix_report_uploads_batch_id", table_name="report_uploads")
    op.drop_table("report_uploads")

    op.drop_index(
        "ix_report_upload_batches_expires_at",
        table_name="report_upload_batches",
    )
    op.drop_index(
        "ix_report_upload_batches_created_by_user_id",
        table_name="report_upload_batches",
    )
    op.drop_index(
        "ix_report_upload_batches_business_id",
        table_name="report_upload_batches",
    )
    op.drop_table("report_upload_batches")
