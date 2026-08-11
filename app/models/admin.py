from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Column, DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.models.base import UUIDPrimaryKey, utc_now
from app.models.user import UserRole

ADMIN_AUDIT_LABEL_MAX_LENGTH = 64
ADMIN_AUDIT_REQUEST_ID_MAX_LENGTH = 128


class AdminAuditEvent(UUIDPrimaryKey, table=True):
    """Immutable platform-administration audit event.

    Actor and business identifiers are intentionally stored as immutable UUID
    snapshots rather than cascading foreign keys. Audit history must survive
    lifecycle changes to the records it describes.
    """

    __tablename__ = "admin_audit_events"
    __table_args__ = (
        CheckConstraint(
            "actor_role IN ('super_admin', 'customer_service')",
            name="ck_admin_audit_events_actor_role",
        ),
        CheckConstraint(
            "action ~ '^[a-z][a-z0-9_.:-]{0,63}$'",
            name="ck_admin_audit_events_action",
        ),
        CheckConstraint(
            "target_type ~ '^[a-z][a-z0-9_.:-]{0,63}$'",
            name="ck_admin_audit_events_target_type",
        ),
        CheckConstraint(
            "request_id IS NULL OR request_id ~ '^[A-Za-z0-9._:-]{1,128}$'",
            name="ck_admin_audit_events_request_id",
        ),
        CheckConstraint(
            "jsonb_typeof(metadata_json) = 'object'",
            name="ck_admin_audit_events_metadata_object",
        ),
        Index(
            "ix_admin_audit_events_actor_created",
            "actor_user_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_admin_audit_events_business_created",
            "business_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_admin_audit_events_action_created",
            "action",
            "created_at",
            "id",
        ),
        Index(
            "ix_admin_audit_events_target_created",
            "target_type",
            "target_id",
            "created_at",
            "id",
        ),
    )

    actor_user_id: UUID = Field(nullable=False, index=True)
    actor_role: UserRole = Field(
        sa_column=Column(String(32), nullable=False),
    )
    action: str = Field(
        sa_column=Column(
            String(ADMIN_AUDIT_LABEL_MAX_LENGTH),
            nullable=False,
        ),
    )
    target_type: str = Field(
        sa_column=Column(
            String(ADMIN_AUDIT_LABEL_MAX_LENGTH),
            nullable=False,
        ),
    )
    target_id: UUID = Field(nullable=False, index=True)
    business_id: UUID | None = Field(
        default=None,
        nullable=True,
        index=True,
    )
    request_id: str | None = Field(
        default=None,
        sa_column=Column(
            String(ADMIN_AUDIT_REQUEST_ID_MAX_LENGTH),
            nullable=True,
        ),
    )
    metadata_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(
            JSONB,
            nullable=False,
            default=dict,
        ),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
        ),
    )
