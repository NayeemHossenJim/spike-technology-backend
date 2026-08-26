from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Integer, String
from sqlmodel import Field

from app.models.base import TimestampedModel, UUIDPrimaryKey

PROFILE_USERNAME_MAX_LENGTH = 80
PROFILE_PHONE_NUMBER_MAX_LENGTH = 32
PROFILE_STREET_ADDRESS_MAX_LENGTH = 255
PROFILE_LOCATION_MAX_LENGTH = 120
PROFILE_POSTAL_CODE_MAX_LENGTH = 32
PROFILE_BIO_MAX_LENGTH = 200


class UserRole(StrEnum):
    USER = "user"
    SUPER_ADMIN = "super_admin"
    CUSTOMER_SERVICE = "customer_service"


class Industry(StrEnum):
    GENERAL = "General"
    FINANCE_BANKING = "Finance & Banking"
    INVESTMENT_MANAGEMENT = "Investment Management"
    INSURANCE = "Insurance"
    ACCOUNTING = "Accounting"
    AGRICULTURE = "Agriculture"
    MEDIA_ENTERTAINMENT = "Media & Entertainment"
    HEALTHCARE = "Healthcare"
    TRANSPORTATION = "Transportation"
    MANUFACTURING = "Manufacturing"
    CONSTRUCTION = "Construction"
    CONSULTANT = "Consultant"
    TECHNOLOGY = "Technology"
    GOVERNMENT = "Government"
    MARKETING = "Marketing"
    OTHER = "Other"


class JobRole(StrEnum):
    EXECUTIVE_C_SUITE = "Executive / C-Suite"
    ACCOUNTANT = "Accountant"
    BOOKKEEPER = "Bookkeeper"
    CONTROLLER = "Controller"
    ANALYST = "Analyst"
    PRODUCT_MANAGER = "Product Manager"
    ENGINEER_DEVELOPER = "Engineer / Developer"
    MARKETER = "Marketer"
    MANUFACTURER = "Manufacturer"
    CFO = "CFO"
    SALES_REVOPS = "Sales & RevOps"
    CONSULTANT = "Consultant"
    OPERATIONS = "Operations"
    FINANCIAL_ADVISOR = "Financial Advisor"
    SUPPORT = "Support"


class User(UUIDPrimaryKey, TimestampedModel, table=True):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "auth_session_version >= 0",
            name="ck_users_auth_session_version",
        ),
        CheckConstraint(
            "username IS NULL OR "
            f"char_length(trim(username)) BETWEEN 1 AND {PROFILE_USERNAME_MAX_LENGTH}",
            name="ck_users_profile_username",
        ),
        CheckConstraint(
            "phone_number IS NULL OR "
            f"char_length(trim(phone_number)) BETWEEN 7 AND {PROFILE_PHONE_NUMBER_MAX_LENGTH}",
            name="ck_users_profile_phone_number",
        ),
        CheckConstraint(
            "street_address IS NULL OR "
            "char_length(trim(street_address)) BETWEEN 1 AND "
            f"{PROFILE_STREET_ADDRESS_MAX_LENGTH}",
            name="ck_users_profile_street_address",
        ),
        CheckConstraint(
            f"city IS NULL OR char_length(trim(city)) BETWEEN 1 AND {PROFILE_LOCATION_MAX_LENGTH}",
            name="ck_users_profile_city",
        ),
        CheckConstraint(
            "state IS NULL OR "
            f"char_length(trim(state)) BETWEEN 1 AND {PROFILE_LOCATION_MAX_LENGTH}",
            name="ck_users_profile_state",
        ),
        CheckConstraint(
            "postal_code IS NULL OR "
            f"char_length(trim(postal_code)) BETWEEN 1 AND {PROFILE_POSTAL_CODE_MAX_LENGTH}",
            name="ck_users_profile_postal_code",
        ),
        CheckConstraint(
            f"bio IS NULL OR char_length(trim(bio)) BETWEEN 1 AND {PROFILE_BIO_MAX_LENGTH}",
            name="ck_users_profile_bio",
        ),
    )

    email: str = Field(
        sa_column=Column(String(320), nullable=False, unique=True, index=True),
    )
    full_name: str = Field(sa_column=Column(String(120), nullable=False))
    username: str | None = Field(
        default=None,
        sa_column=Column(String(PROFILE_USERNAME_MAX_LENGTH), nullable=True),
    )
    phone_number: str | None = Field(
        default=None,
        sa_column=Column(String(PROFILE_PHONE_NUMBER_MAX_LENGTH), nullable=True),
    )
    street_address: str | None = Field(
        default=None,
        sa_column=Column(String(PROFILE_STREET_ADDRESS_MAX_LENGTH), nullable=True),
    )
    city: str | None = Field(
        default=None,
        sa_column=Column(String(PROFILE_LOCATION_MAX_LENGTH), nullable=True),
    )
    state: str | None = Field(
        default=None,
        sa_column=Column(String(PROFILE_LOCATION_MAX_LENGTH), nullable=True),
    )
    postal_code: str | None = Field(
        default=None,
        sa_column=Column(String(PROFILE_POSTAL_CODE_MAX_LENGTH), nullable=True),
    )
    bio: str | None = Field(
        default=None,
        sa_column=Column(String(PROFILE_BIO_MAX_LENGTH), nullable=True),
    )
    password_hash: str = Field(sa_column=Column(String(255), nullable=False))
    role: UserRole = Field(
        default=UserRole.USER,
        sa_column=Column(String(32), nullable=False, default=UserRole.USER.value),
    )
    auth_session_version: int = Field(
        default=0,
        sa_column=Column(
            Integer,
            nullable=False,
            default=0,
            server_default="0",
        ),
    )
    industry: Industry | None = Field(
        default=None,
        sa_column=Column(String(120), nullable=True),
    )
    job_role: JobRole | None = Field(
        default=None,
        sa_column=Column(String(120), nullable=True),
    )
    terms_accepted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    terms_version: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
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
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )
