from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.core.password_policy import validate_password_strength
from app.models.user import (
    PROFILE_BIO_MAX_LENGTH,
    PROFILE_LOCATION_MAX_LENGTH,
    PROFILE_PHONE_NUMBER_MAX_LENGTH,
    PROFILE_POSTAL_CODE_MAX_LENGTH,
    PROFILE_STREET_ADDRESS_MAX_LENGTH,
    PROFILE_USERNAME_MAX_LENGTH,
    Industry,
    JobRole,
)


class UserProfileUpdate(BaseModel):
    """Fields editable from Figma's self-service Edit Profile surface.

    Email and authentication/authorization fields are intentionally absent.
    They require separate verified security workflows.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    username: str | None = Field(
        default=None,
        min_length=1,
        max_length=PROFILE_USERNAME_MAX_LENGTH,
    )
    phone_number: str | None = Field(
        default=None,
        min_length=7,
        max_length=PROFILE_PHONE_NUMBER_MAX_LENGTH,
    )
    industry: Industry | None = None
    job_role: JobRole | None = None
    street_address: str | None = Field(
        default=None,
        min_length=1,
        max_length=PROFILE_STREET_ADDRESS_MAX_LENGTH,
    )
    city: str | None = Field(
        default=None,
        min_length=1,
        max_length=PROFILE_LOCATION_MAX_LENGTH,
    )
    state: str | None = Field(
        default=None,
        min_length=1,
        max_length=PROFILE_LOCATION_MAX_LENGTH,
    )
    postal_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=PROFILE_POSTAL_CODE_MAX_LENGTH,
    )
    bio: str | None = Field(
        default=None,
        min_length=1,
        max_length=PROFILE_BIO_MAX_LENGTH,
    )

    @field_validator("full_name", mode="before")
    @classmethod
    def normalize_full_name(cls, value: object) -> object:
        if value is None:
            raise ValueError("Full name cannot be null.")
        return value.strip() if isinstance(value, str) else value

    @field_validator(
        "username",
        "phone_number",
        "street_address",
        "city",
        "state",
        "postal_code",
        "bio",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_change(self) -> UserProfileUpdate:
        if not self.model_fields_set:
            raise ValueError("At least one profile field must be updated.")
        return self


class UserSecurityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserEmailChangeRequest(UserSecurityRequest):
    old_email: EmailStr
    new_email: EmailStr
    current_password: str = Field(min_length=1, max_length=128)


class UserEmailChangeConfirm(UserSecurityRequest):
    new_email: EmailStr
    otp: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class UserEmailTwoStepEnableRequest(UserSecurityRequest):
    email: EmailStr
    current_password: str = Field(min_length=1, max_length=128)


class UserEmailTwoStepDisableRequest(UserSecurityRequest):
    current_password: str = Field(min_length=1, max_length=128)


class UserEmailTwoStepChallengeRequest(UserSecurityRequest):
    challenge_id: UUID


class UserEmailTwoStepConfirmRequest(UserEmailTwoStepChallengeRequest):
    otp: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class UserEmailTwoStepStatusResponse(BaseModel):
    enabled: bool
    enabled_at: datetime | None
    verification_method: Literal["email_code"] = "email_code"
    masked_destination: str
    reauthentication_required: bool = False


class UserPasswordChangeRequest(UserSecurityRequest):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)
    confirm_password: str = Field(min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_new_password_strength(cls, value: str) -> str:
        return validate_password_strength(value)

    @model_validator(mode="after")
    def passwords_must_match(self) -> UserPasswordChangeRequest:
        if self.new_password != self.confirm_password:
            raise ValueError("New password and confirmation must match.")
        if self.new_password == self.current_password:
            raise ValueError("New password must differ from the current password.")
        return self


class UserAccountDeleteRequest(UserSecurityRequest):
    current_password: str = Field(min_length=1, max_length=128)
    confirmation: Literal["DELETE"]
