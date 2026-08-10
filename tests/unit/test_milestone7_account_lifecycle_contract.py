from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint

from app.core.config import AppEnvironment, Settings
from app.core.security import (
    TokenType,
    create_jwt_token,
    decode_jwt_token,
)
from app.main import create_app
from app.models.user import User
from app.schemas.admin import (
    AdminAccountActionReason,
    AdminAccountActionRequest,
)


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        stripe_enabled=False,
        app_env=AppEnvironment.TEST,
        database_url=("postgresql+asyncpg://spike:password@localhost:5432/spike_test"),
        redis_url="redis://localhost:6379/15",
        celery_broker_url="redis://localhost:6379/15",
        celery_result_backend="redis://localhost:6379/14",
        jwt_secret_key=("test-only-secret-with-at-least-thirty-two-characters"),
    )


def test_user_model_has_nonnegative_session_generation() -> None:
    assert "auth_session_version" in User.__table__.c
    assert User.__table__.c.auth_session_version.nullable is False

    constraints = {
        constraint.name
        for constraint in User.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "ck_users_auth_session_version" in constraints


def test_jwt_round_trip_preserves_session_generation() -> None:
    settings = make_settings()

    issued = create_jwt_token(
        user_id=uuid4(),
        token_type=TokenType.ACCESS,
        settings=settings,
        expires_delta=timedelta(minutes=15),
        session_version=7,
    )

    decoded = decode_jwt_token(
        issued.raw_token,
        settings,
    )

    assert decoded.session_version == 7


@pytest.mark.parametrize(
    "version",
    [-1, True],
)
def test_jwt_rejects_invalid_session_generation(
    version: object,
) -> None:
    with pytest.raises(ValueError):
        create_jwt_token(
            user_id=uuid4(),
            token_type=TokenType.ACCESS,
            settings=make_settings(),
            expires_delta=timedelta(minutes=15),
            session_version=version,  # type: ignore[arg-type]
        )


def test_admin_account_action_reason_is_closed_enum() -> None:
    assert {reason.value for reason in AdminAccountActionReason} == {
        "security_review",
        "terms_violation",
        "abuse_prevention",
        "administrative_hold",
        "support_resolution",
    }

    with pytest.raises(ValidationError):
        AdminAccountActionRequest.model_validate({"reason_code": "free form sensitive note"})


def test_stage3_openapi_exposes_super_admin_lifecycle_routes() -> None:
    paths = create_app().openapi()["paths"]

    assert "post" in paths["/api/v1/admin/users/{user_id}/suspend"]
    assert "post" in paths["/api/v1/admin/users/{user_id}/reactivate"]


def test_stage3_migration_is_linear_and_adds_generation() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (root / "alembic" / "versions" / "0014_m7_account_lifecycle.py").read_text(
        encoding="utf-8"
    )

    assert 'revision: str = "0014_m7_account_lifecycle"' in migration
    assert ('down_revision: str | Sequence[str] | None = "0013_m7_admin_audit"') in migration
    assert '"auth_session_version"' in migration
    assert "ck_users_auth_session_version" in migration
