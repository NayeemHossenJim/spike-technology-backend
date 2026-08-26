from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Index, Integer, String
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


class EmailChangeOTP(UUIDPrimaryKey, TimestampedModel, table=True):
    __tablename__ = "email_change_tokens"

    user_id: UUID = Field(foreign_key="users.id", index=True)
    new_email: str = Field(sa_column=Column(String(320), nullable=False))
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


class EmailTwoStepPurpose(StrEnum):
    ENABLE = "enable"
    DISABLE = "disable"
    LOGIN = "login"


class EmailTwoStepChallenge(UUIDPrimaryKey, TimestampedModel, table=True):
    """One bounded email-code challenge for a specific account-security action."""

    __tablename__ = "email_two_step_challenges"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('enable', 'disable', 'login')",
            name="ck_email_two_step_challenges_purpose",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_email_two_step_challenges_attempt_count",
        ),
        CheckConstraint(
            "session_version >= 0",
            name="ck_email_two_step_challenges_session_version",
        ),
        Index(
            "ix_email_two_step_challenges_user_purpose_created",
            "user_id",
            "purpose",
            "created_at",
        ),
    )

    user_id: UUID = Field(foreign_key="users.id", index=True)
    purpose: EmailTwoStepPurpose = Field(
        sa_column=Column(String(16), nullable=False),
    )
    otp_hash: str = Field(sa_column=Column(String(512), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    attempt_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    session_version: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    remember_me: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="false"),
    )
    used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
