from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.deps import require_roles
from app.models.user import User, UserRole


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
