from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.business import TenantRole
from app.models.user import Industry
from app.schemas.subscription import SubscriptionRead


class BusinessCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=160)
    industry: Industry | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class BusinessRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_user_id: UUID
    name: str
    industry: Industry | None


class RoleAssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_id: UUID
    user_id: UUID
    role: TenantRole
    is_active: bool


class BusinessContextRead(BaseModel):
    business: BusinessRead
    role_assignment: RoleAssignmentRead
    subscription: SubscriptionRead
