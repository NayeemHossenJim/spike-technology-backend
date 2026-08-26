from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import create_app
from app.models.auth import EmailChangeOTP
from app.models.user import User
from app.schemas.user import (
    UserAccountDeleteRequest,
    UserEmailChangeConfirm,
    UserEmailChangeRequest,
    UserPasswordChangeRequest,
)
from app.services.billing import BillingCheckoutConflictError, CheckoutService


def test_email_change_request_requires_both_addresses_and_current_password() -> None:
    payload = UserEmailChangeRequest.model_validate(
        {
            "old_email": "Old@Example.com",
            "new_email": "New@Example.com",
            "current_password": "CorrectHorseBattery9",
        }
    )

    assert str(payload.old_email) == "Old@example.com"
    assert str(payload.new_email) == "New@example.com"
    assert payload.current_password == "CorrectHorseBattery9"


@pytest.mark.parametrize(
    "payload",
    [
        {"old_email": "old@example.com", "new_email": "new@example.com"},
        {
            "old_email": "not-an-email",
            "new_email": "new@example.com",
            "current_password": "secret",
        },
        {
            "old_email": "old@example.com",
            "new_email": "new@example.com",
            "current_password": "secret",
            "role": "super_admin",
        },
    ],
)
def test_email_change_request_rejects_incomplete_or_extra_data(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        UserEmailChangeRequest.model_validate(payload)


def test_email_change_confirmation_requires_six_digit_otp() -> None:
    accepted = UserEmailChangeConfirm(
        new_email="new@example.com",
        otp="012345",
    )
    assert accepted.otp == "012345"

    for invalid_otp in ("12345", "1234567", "abcdef"):
        with pytest.raises(ValidationError):
            UserEmailChangeConfirm(
                new_email="new@example.com",
                otp=invalid_otp,
            )


def test_password_change_enforces_strength_confirmation_and_difference() -> None:
    accepted = UserPasswordChangeRequest(
        current_password="CorrectHorseBattery9",
        new_password="DifferentHorseBattery8",
        confirm_password="DifferentHorseBattery8",
    )
    assert accepted.new_password == "DifferentHorseBattery8"

    for invalid in (
        {
            "current_password": "CorrectHorseBattery9",
            "new_password": "DifferentHorseBattery8",
            "confirm_password": "MismatchHorseBattery8",
        },
        {
            "current_password": "CorrectHorseBattery9",
            "new_password": "CorrectHorseBattery9",
            "confirm_password": "CorrectHorseBattery9",
        },
        {
            "current_password": "CorrectHorseBattery9",
            "new_password": "alllowercase123",
            "confirm_password": "alllowercase123",
        },
    ):
        with pytest.raises(ValidationError):
            UserPasswordChangeRequest.model_validate(invalid)


def test_account_deletion_requires_explicit_literal_confirmation() -> None:
    accepted = UserAccountDeleteRequest(
        current_password="CorrectHorseBattery9",
        confirmation="DELETE",
    )
    assert accepted.confirmation == "DELETE"

    with pytest.raises(ValidationError):
        UserAccountDeleteRequest(
            current_password="CorrectHorseBattery9",
            confirmation="delete",  # type: ignore[arg-type]
        )


def test_account_security_models_store_pending_email_and_deletion_timestamp() -> None:
    assert EmailChangeOTP.__table__.c.new_email.type.length == 320
    assert EmailChangeOTP.__table__.c.otp_hash.type.length == 512
    assert EmailChangeOTP.__table__.c.attempt_count.nullable is False
    assert EmailChangeOTP.__table__.c.used_at.nullable is True
    assert User.__table__.c.deleted_at.nullable is True
    assert User.__table__.c.deleted_at.index is True


@pytest.mark.asyncio
async def test_checkout_revalidation_rejects_a_concurrently_deleted_user() -> None:
    deleted_user = User(
        id=uuid4(),
        email="deleted-source@example.com",
        full_name="Deleted Source",
        password_hash="unused",
        is_active=False,
        deleted_at=datetime.now(UTC),
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = deleted_user
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result
    service = CheckoutService(
        session=session,
        settings=MagicMock(),
        gateway=MagicMock(),
    )

    with pytest.raises(BillingCheckoutConflictError):
        await service._lock_active_user(deleted_user.id)


def test_gap04_openapi_exposes_separate_authenticated_security_routes() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]

    operations = {
        "/api/v1/users/me/email-change": "post",
        "/api/v1/users/me/email-change/confirm": "post",
        "/api/v1/users/me/password": "put",
        "/api/v1/users/me": "delete",
    }
    for path, method in operations.items():
        assert paths[path][method]["security"] == [{"HTTPBearer": []}]

    schemas = schema["components"]["schemas"]
    assert set(schemas["UserEmailChangeRequest"]["required"]) == {
        "old_email",
        "new_email",
        "current_password",
    }
    assert set(schemas["UserPasswordChangeRequest"]["required"]) == {
        "current_password",
        "new_password",
        "confirm_password",
    }
    assert set(schemas["UserAccountDeleteRequest"]["required"]) == {
        "current_password",
        "confirmation",
    }


def test_gap04_migration_is_linear_and_reversible() -> None:
    project_root = Path(__file__).resolve().parents[2]
    migration = (
        project_root / "alembic" / "versions" / "0018_gap04_account_security.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0018_gap04_account_security"' in migration
    assert (
        'down_revision: str | Sequence[str] | None = "0017_gap03_self_service_profile"' in migration
    )
    assert '"email_change_tokens"' in migration
    assert '"deleted_at"' in migration
    assert 'op.drop_table("email_change_tokens")' in migration
    assert 'op.drop_column("users", "deleted_at")' in migration
