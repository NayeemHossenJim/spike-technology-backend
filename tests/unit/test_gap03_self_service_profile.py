from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import create_app
from app.models.business import Business
from app.models.user import Industry, User
from app.schemas.user import UserProfileUpdate
from app.services.user_profiles import UserProfileService


def test_profile_update_normalizes_only_figma_profile_fields() -> None:
    payload = UserProfileUpdate.model_validate(
        {
            "full_name": "  Brendoly Cooper  ",
            "username": "  B. Cooper  ",
            "phone_number": "  (207) 555-0119  ",
            "industry": "Accounting",
            "job_role": "Accountant",
            "street_address": "  2972 Westheimer Rd.  ",
            "city": "  Santa Ana  ",
            "state": "  Illinois  ",
            "postal_code": "  85486  ",
            "bio": "  Finance operations lead.  ",
        }
    )

    assert payload.model_dump() == {
        "full_name": "Brendoly Cooper",
        "username": "B. Cooper",
        "phone_number": "(207) 555-0119",
        "industry": "Accounting",
        "job_role": "Accountant",
        "street_address": "2972 Westheimer Rd.",
        "city": "Santa Ana",
        "state": "Illinois",
        "postal_code": "85486",
        "bio": "Finance operations lead.",
    }


def test_profile_update_supports_explicit_optional_clears() -> None:
    payload = UserProfileUpdate(
        username=None,
        phone_number=None,
        industry=None,
        job_role=None,
        street_address=None,
        city=None,
        state=None,
        postal_code=None,
        bio=None,
    )

    assert payload.model_dump(exclude_unset=True) == {
        "username": None,
        "phone_number": None,
        "industry": None,
        "job_role": None,
        "street_address": None,
        "city": None,
        "state": None,
        "postal_code": None,
        "bio": None,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"full_name": None},
        {"full_name": " "},
        {"phone_number": "123"},
        {"industry": "Space Mining"},
        {"job_role": "Wizard"},
        {"bio": "x" * 201},
        {"email": "replacement@example.com"},
        {"role": "super_admin"},
        {"is_active": False},
    ],
)
def test_profile_update_rejects_empty_invalid_or_sensitive_mutations(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        UserProfileUpdate.model_validate(payload)


def test_user_model_persists_bounded_nullable_profile_fields() -> None:
    expected_lengths = {
        "username": 80,
        "phone_number": 32,
        "street_address": 255,
        "city": 120,
        "state": 120,
        "postal_code": 32,
        "bio": 200,
    }

    for field_name, expected_length in expected_lengths.items():
        column = User.__table__.c[field_name]
        assert column.nullable is True
        assert column.type.length == expected_length

    constraints = {
        constraint.name
        for constraint in User.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_users_profile_username",
        "ck_users_profile_phone_number",
        "ck_users_profile_street_address",
        "ck_users_profile_city",
        "ck_users_profile_state",
        "ck_users_profile_postal_code",
        "ck_users_profile_bio",
    } <= constraints


@pytest.mark.asyncio
async def test_profile_service_updates_user_and_business_industry_atomically() -> None:
    user_id = uuid4()
    user = User(
        id=user_id,
        email="profile@example.com",
        full_name="Original",
        password_hash="hash",
    )
    business = Business(
        owner_user_id=user_id,
        name="Profile Tenant",
        industry=Industry.TECHNOLOGY,
    )
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    business_result = MagicMock()
    business_result.scalar_one_or_none.return_value = business
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [user_result, business_result]

    updated = await UserProfileService(session).update(
        user_id,
        payload=UserProfileUpdate(
            full_name="  Updated Name  ",
            username="  Account Label  ",
            industry=Industry.ACCOUNTING,
        ),
    )

    assert updated is user
    assert user.full_name == "Updated Name"
    assert user.username == "Account Label"
    assert user.industry == Industry.ACCOUNTING
    assert business.industry == Industry.ACCOUNTING
    assert session.execute.await_count == 2
    session.commit.assert_awaited_once_with()
    session.refresh.assert_awaited_once_with(user)
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_service_does_not_touch_business_when_industry_is_omitted() -> None:
    user_id = uuid4()
    user = User(
        id=user_id,
        email="profile@example.com",
        full_name="Original",
        password_hash="hash",
        industry=Industry.TECHNOLOGY,
    )
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = user_result

    await UserProfileService(session).update(
        user_id,
        payload=UserProfileUpdate(bio="Updated biography"),
    )

    assert user.industry == Industry.TECHNOLOGY
    assert user.bio == "Updated biography"
    session.execute.assert_awaited_once()


def test_gap03_openapi_keeps_authenticated_profile_mutation_separate() -> None:
    schema = create_app().openapi()
    me_path = schema["paths"]["/api/v1/users/me"]

    assert set(me_path) == {"get", "patch", "delete"}
    request_schema = schema["components"]["schemas"]["UserProfileUpdate"]
    assert list(request_schema["properties"]) == [
        "full_name",
        "username",
        "phone_number",
        "industry",
        "job_role",
        "street_address",
        "city",
        "state",
        "postal_code",
        "bio",
    ]
    assert {
        "email",
        "password",
        "password_hash",
        "role",
        "is_active",
        "is_verified",
        "auth_session_version",
    }.isdisjoint(request_schema["properties"])
    assert me_path["patch"]["security"] == [{"HTTPBearer": []}]


def test_gap03_migration_is_linear_and_reversible() -> None:
    project_root = Path(__file__).resolve().parents[2]
    migration = (
        project_root / "alembic" / "versions" / "0017_gap03_self_service_profile.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0017_gap03_self_service_profile"' in migration
    assert 'down_revision: str | Sequence[str] | None = "0016_gap02_grounded_dashboards"' in (
        migration
    )
    for field_name in (
        "username",
        "phone_number",
        "street_address",
        "city",
        "state",
        "postal_code",
        "bio",
    ):
        assert f'"{field_name}"' in migration
    assert "op.drop_column" in migration
