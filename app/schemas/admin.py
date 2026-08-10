from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.business import TenantRole
from app.models.user import Industry, UserRole


class AdminUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    is_verified: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminUserPageRead(BaseModel):
    items: list[AdminUserRead]
    total: int
    limit: int
    offset: int


class AdminBusinessRead(BaseModel):
    id: UUID
    name: str
    industry: Industry | None
    owner_user_id: UUID
    owner: AdminUserRead
    role_assignment_role: TenantRole
    role_assignment_is_active: bool
    created_at: datetime
    updated_at: datetime


class AdminBusinessPageRead(BaseModel):
    items: list[AdminBusinessRead]
    total: int
    limit: int
    offset: int


class AdminAccountActionReason(StrEnum):
    SECURITY_REVIEW = "security_review"
    TERMS_VIOLATION = "terms_violation"
    ABUSE_PREVENTION = "abuse_prevention"
    ADMINISTRATIVE_HOLD = "administrative_hold"
    SUPPORT_RESOLUTION = "support_resolution"


class AdminAccountActionRequest(BaseModel):
    reason_code: AdminAccountActionReason


class AdminAccountActionRead(BaseModel):
    user: AdminUserRead
    changed: bool
    sessions_revoked: int
