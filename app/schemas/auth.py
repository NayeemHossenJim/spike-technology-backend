from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.password_policy import validate_password_strength
from app.models.user import Industry, JobRole, UserRole


class AuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RegisterRequest(AuthRequest):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    industry: Industry | None = None
    job_role: JobRole | None = Field(
        default=None,
        validation_alias=AliasChoices("job_role", "job_title"),
    )

    @field_validator("full_name", mode="before")
    @classmethod
    def strip_full_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def validate_registration_password(cls, value: str) -> str:
        return validate_password_strength(value)


class LoginRequest(AuthRequest):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    remember_me: bool = False


class ForgotPasswordRequest(AuthRequest):
    email: EmailStr


class EmailOTPRequest(AuthRequest):
    email: EmailStr
    otp: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class VerifyEmailOTPRequest(EmailOTPRequest):
    pass


class VerifyPasswordResetOTPRequest(EmailOTPRequest):
    pass


class ResetPasswordRequest(EmailOTPRequest):
    new_password: str = Field(min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_new_password_strength(cls, value: str) -> str:
        return validate_password_strength(value)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    role: UserRole
    industry: Industry | None
    job_role: JobRole | None
    is_active: bool
    is_verified: bool


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    access_token_expires_in: int


class MessageResponse(BaseModel):
    message: str
