from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles, require_tenant_roles
from app.models.business import Business, RoleAssignment, TenantRole
from app.models.user import User, UserRole
from app.services.tenant import (
    TenantAccessForbiddenError,
    TenantContext,
    load_tenant_context,
)


def make_user(role: UserRole) -> User:
    return User(
        email=f"{role.value}@example.com",
        full_name="Role Test",
        password_hash="not-used-in-this-test",
        role=role,
        is_verified=True,
    )


@pytest.mark.asyncio
async def test_role_guard_rejects_user_without_required_role() -> None:
    guard = require_roles(UserRole.SUPER_ADMIN)
    with pytest.raises(HTTPException) as exc_info:
        await guard(current_user=make_user(UserRole.USER))

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_role_guard_accepts_required_role() -> None:
    guard = require_roles(UserRole.SUPER_ADMIN)
    admin = make_user(UserRole.SUPER_ADMIN)
    assert await guard(current_user=admin) is admin


@pytest.mark.asyncio
async def test_tenant_role_guard_uses_business_assignment_not_global_role() -> None:
    user = make_user(UserRole.USER)
    business = Business(owner_user_id=user.id, name="Tenant")
    assignment = RoleAssignment(
        business_id=business.id,
        user_id=user.id,
        role=TenantRole.OWNER,
    )
    context = TenantContext(business=business, role_assignment=assignment)

    guard = require_tenant_roles(TenantRole.OWNER)
    assert await guard(tenant=context) is context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [UserRole.SUPER_ADMIN, UserRole.CUSTOMER_SERVICE],
)
async def test_platform_roles_cannot_resolve_tenant_context(role: UserRole) -> None:
    session = MagicMock(spec=AsyncSession)
    session.execute = AsyncMock()

    with pytest.raises(TenantAccessForbiddenError):
        await load_tenant_context(session, make_user(role))

    session.execute.assert_not_awaited()
