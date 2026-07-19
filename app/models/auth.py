from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlmodel import Field

from app.models.base import TimestampedModel, UUIDPrimaryKey


class RefreshToken(UUIDPrimaryKey, TimestampedModel, table=True):
    __tablename__ = "refresh_tokens"

    user_id: UUID = Field(foreign_key="users.id", index=True)
    token_id: UUID = Field(index=True, unique=True)
    token_hash: str = Field(sa_column=Column(String(64), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    is_persistent: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="false"),
    )
    revoked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class EmailVerificationOTP(UUIDPrimaryKey, TimestampedModel, table=True):
    __tablename__ = "email_verification_tokens"

    user_id: UUID = Field(foreign_key="users.id", index=True)
    otp_hash: str = Field(sa_column=Column(String(512), nullable=False, unique=True))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    attempt_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class PasswordResetOTP(UUIDPrimaryKey, TimestampedModel, table=True):
    __tablename__ = "password_reset_tokens"

    user_id: UUID = Field(foreign_key="users.id", index=True)
    otp_hash: str = Field(sa_column=Column(String(512), nullable=False, unique=True))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    attempt_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    verified_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
