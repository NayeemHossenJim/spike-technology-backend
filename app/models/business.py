from __future__ import annotations

from uuid import UUID

from sqlalchemy import Column, String
from sqlmodel import Field

from app.models.base import TimestampedModel, UUIDPrimaryKey


class Business(UUIDPrimaryKey, TimestampedModel, table=True):
    """One business may be owned by one end-user; creation is added with onboarding in Phase 2."""

    __tablename__ = "businesses"

    owner_user_id: UUID = Field(foreign_key="users.id", index=True, unique=True)
    name: str = Field(sa_column=Column(String(160), nullable=False))
    industry: str | None = Field(default=None, sa_column=Column(String(120), nullable=True))
