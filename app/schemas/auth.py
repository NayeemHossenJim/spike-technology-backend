from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import UserRole


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    industry: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=120)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        if not any(char.islower() for char in value):
            raise ValueError("Password must include a lowercase letter")
        if not any(char.isupper() for char in value):
            raise ValueError("Password must include an uppercase letter")
        if not any(char.isdigit() for char in value):
            raise ValueError("Password must include a number")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(RefreshRequest):
    pass


class OpaqueTokenRequest(BaseModel):
    token: str = Field(min_length=32, max_length=512)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(OpaqueTokenRequest):
    new_password: str = Field(min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_new_password_strength(cls, value: str) -> str:
        return RegisterRequest.validate_password_strength(value)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    role: UserRole
    industry: str | None
    job_title: str | None
    is_active: bool
    is_verified: bool


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    access_token_expires_in: int


class MessageResponse(BaseModel):
    message: str
