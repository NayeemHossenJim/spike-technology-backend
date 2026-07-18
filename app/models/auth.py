from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Column, DateTime, String
from sqlmodel import Field

from app.models.base import TimestampedModel, UUIDPrimaryKey


class RefreshToken(UUIDPrimaryKey, TimestampedModel, table=True):
    __tablename__ = "refresh_tokens"

    user_id: UUID = Field(foreign_key="users.id", index=True)
    token_id: UUID = Field(index=True, unique=True)
    token_hash: str = Field(sa_column=Column(String(64), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    revoked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class EmailVerificationToken(UUIDPrimaryKey, TimestampedModel, table=True):
    __tablename__ = "email_verification_tokens"

    user_id: UUID = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(sa_column=Column(String(64), nullable=False, unique=True))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class PasswordResetToken(UUIDPrimaryKey, TimestampedModel, table=True):
    __tablename__ = "password_reset_tokens"

    user_id: UUID = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(sa_column=Column(String(64), nullable=False, unique=True))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
