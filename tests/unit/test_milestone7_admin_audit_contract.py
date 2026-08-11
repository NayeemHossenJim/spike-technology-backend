from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql import JSONB

from app.models.admin import AdminAuditEvent
from app.models.user import User, UserRole
from app.services.admin_audit import (
    AdminAuditActorError,
    AdminAuditLabelError,
    AdminAuditMetadataError,
    AdminAuditRequestIdError,
    normalize_admin_audit_label,
    normalize_admin_audit_request_id,
    validate_admin_audit_actor,
    validate_admin_audit_metadata,
)


def platform_user(role: UserRole) -> User:
    return User(
        id=uuid4(),
        email=f"{role.value}@example.com",
        full_name="Platform Operator",
        password_hash="unused",
        role=role,
        is_active=True,
        is_verified=True,
    )


def test_admin_audit_model_is_append_only_shaped() -> None:
    columns = AdminAuditEvent.__table__.c

    assert "created_at" in columns
    assert "updated_at" not in columns
    assert isinstance(columns["metadata_json"].type, JSONB)
    assert columns["actor_user_id"].nullable is False
    assert columns["target_id"].nullable is False
    assert columns["business_id"].nullable is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        " User.Suspend ",
        "user suspend",
        "USER_SUSPEND",
        "1invalid",
        "x" * 65,
    ],
)
def test_admin_audit_label_validation_fails_closed(value: str) -> None:
    with pytest.raises(AdminAuditLabelError):
        normalize_admin_audit_label(value)


def test_admin_audit_request_id_matches_application_contract() -> None:
    assert normalize_admin_audit_request_id("request-123.alpha") == ("request-123.alpha")
    assert normalize_admin_audit_request_id(None) is None

    with pytest.raises(AdminAuditRequestIdError):
        normalize_admin_audit_request_id("contains spaces")


def test_admin_audit_metadata_is_safe_and_defensively_copied() -> None:
    source: dict[str, object] = {
        "reason_code": "manual_review",
        "state": {
            "from": "active",
            "to": "suspended",
        },
        "attempt": 1,
    }

    validated = validate_admin_audit_metadata(source)

    state = source["state"]
    assert isinstance(state, dict)
    state["to"] = "changed-after-validation"

    assert validated == {
        "reason_code": "manual_review",
        "state": {
            "from": "active",
            "to": "suspended",
        },
        "attempt": 1,
    }


@pytest.mark.parametrize(
    "metadata",
    [
        {"password": "secret"},
        {"credentials": {"access_token": "secret"}},
        {"otp_code": "123456"},
        {"prompt": "sensitive AI prompt"},
        {"nested": {"message_content": "sensitive"}},
        {"api_key": "secret"},
    ],
)
def test_admin_audit_metadata_rejects_sensitive_keys(
    metadata: dict[str, object],
) -> None:
    with pytest.raises(AdminAuditMetadataError):
        validate_admin_audit_metadata(metadata)


def test_admin_audit_actor_must_be_verified_active_platform_operator() -> None:
    validate_admin_audit_actor(platform_user(UserRole.SUPER_ADMIN))
    validate_admin_audit_actor(platform_user(UserRole.CUSTOMER_SERVICE))

    with pytest.raises(AdminAuditActorError):
        validate_admin_audit_actor(platform_user(UserRole.USER))

    inactive = platform_user(UserRole.SUPER_ADMIN)
    inactive.is_active = False

    with pytest.raises(AdminAuditActorError):
        validate_admin_audit_actor(inactive)
