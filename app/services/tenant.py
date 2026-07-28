from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel, select

from app.models.business import Business, RoleAssignment, TenantRole
from app.models.user import User, UserRole


class TenantContextMissingError(Exception):
    pass


class TenantIntegrityError(Exception):
    pass


class TenantAccessForbiddenError(Exception):
    pass


def tenant_select[ModelT: SQLModel](
    model: type[ModelT],
    business_id: UUID,
    *criteria: Any,
):
    """Build a tenant-scoped SELECT and refuse models without a business boundary."""

    business_column = getattr(model, "business_id", None)
    if business_column is None:
        raise TypeError(f"{model.__name__} is not tenant-scoped")
    return select(model).where(business_column == business_id, *criteria)


@dataclass(frozen=True, slots=True)
class TenantScope:
    business_id: UUID

    def select[ModelT: SQLModel](self, model: type[ModelT], *criteria: Any):
        return tenant_select(model, self.business_id, *criteria)

    async def get[ModelT: SQLModel](
        self,
        session: AsyncSession,
        model: type[ModelT],
        record_id: UUID,
    ) -> ModelT | None:
        statement = self.select(model, model.id == record_id)  # type: ignore[attr-defined]
        result = await session.execute(statement)
        return result.scalar_one_or_none()


@dataclass(frozen=True, slots=True)
class TenantContext:
    business: Business
    role_assignment: RoleAssignment

    @property
    def scope(self) -> TenantScope:
        return TenantScope(business_id=self.business.id)


async def load_tenant_context(session: AsyncSession, user: User) -> TenantContext:
    """Resolve a tenant only from the authenticated user's active assignment."""

    if user.role != UserRole.USER:
        raise TenantAccessForbiddenError

    result = await session.execute(
        select(RoleAssignment, Business)
        .join(Business, Business.id == RoleAssignment.business_id)
        .where(
            RoleAssignment.user_id == user.id,
            RoleAssignment.is_active.is_(True),
        )
    )
    row = result.one_or_none()
    if row is None:
        raise TenantContextMissingError

    assignment, business = row
    if (
        assignment.business_id != business.id
        or assignment.user_id != user.id
        or assignment.role != TenantRole.OWNER
        or business.owner_user_id != user.id
    ):
        raise TenantIntegrityError
    return TenantContext(business=business, role_assignment=assignment)
