from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Column, DateTime, String
from sqlmodel import Field

from app.models.base import TimestampedModel, UUIDPrimaryKey


class UserRole(StrEnum):
    USER = "user"
    SUPER_ADMIN = "super_admin"
    CUSTOMER_SERVICE = "customer_service"


class User(UUIDPrimaryKey, TimestampedModel, table=True):
    __tablename__ = "users"

    email: str = Field(
        sa_column=Column(String(320), nullable=False, unique=True, index=True),
    )
    full_name: str = Field(sa_column=Column(String(120), nullable=False))
    password_hash: str = Field(sa_column=Column(String(255), nullable=False))
    role: UserRole = Field(
        default=UserRole.USER,
        sa_column=Column(String(32), nullable=False, default=UserRole.USER.value),
    )
    industry: str | None = Field(default=None, sa_column=Column(String(120), nullable=True))
    job_title: str | None = Field(default=None, sa_column=Column(String(120), nullable=True))
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, default=True),
    )
    is_verified: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, default=False),
    )
    last_login_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
