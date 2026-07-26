from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Column, String, UniqueConstraint
from sqlmodel import Field

from app.models.base import TenantOwnedModel, TimestampedModel, UUIDPrimaryKey
from app.models.user import Industry


class TenantRole(StrEnum):
    """Business-scoped roles approved for the single-business v1 model."""

    OWNER = "owner"


class Business(UUIDPrimaryKey, TimestampedModel, table=True):
    """A v1 tenant owned by exactly one end-user."""

    __tablename__ = "businesses"

    owner_user_id: UUID = Field(foreign_key="users.id", index=True, unique=True)
    name: str = Field(sa_column=Column(String(160), nullable=False))
    industry: Industry | None = Field(
        default=None,
        sa_column=Column(String(120), nullable=True),
    )


class RoleAssignment(
    TenantOwnedModel,
    UUIDPrimaryKey,
    TimestampedModel,
    table=True,
):
    """A user's server-side tenant boundary.

    v1 intentionally assigns only the business owner. The unique user constraint
    enforces the approved one-business-per-user rule at the database layer.
    """

    __tablename__ = "role_assignments"
    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "user_id",
            name="uq_role_assignments_business_user",
        ),
        CheckConstraint("role = 'owner'", name="ck_role_assignments_v1_role"),
    )

    user_id: UUID = Field(
        foreign_key="users.id",
        ondelete="CASCADE",
        nullable=False,
        index=True,
        unique=True,
    )
    role: TenantRole = Field(
        default=TenantRole.OWNER,
        sa_column=Column(
            String(32),
            nullable=False,
            default=TenantRole.OWNER.value,
        ),
    )
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, default=True),
    )
