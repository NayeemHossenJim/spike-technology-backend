from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.main import create_app
from app.models.ai import AICreditAdjustmentReason
from app.schemas.admin import AdminAICreditAdjustmentRequest


def test_admin_ai_credit_adjustment_route_is_post_only() -> None:
    path = create_app().openapi()["paths"][
        "/api/v1/admin/businesses/{business_id}/ai-credits/adjustments"
    ]

    assert set(path) == {"post"}


def test_admin_ai_credit_adjustment_payload_is_closed_and_bounded() -> None:
    payload = AdminAICreditAdjustmentRequest(
        delta=5,
        reason_code=AICreditAdjustmentReason.SUPPORT_CREDIT,
    )

    assert payload.delta == 5

    with pytest.raises(ValidationError):
        AdminAICreditAdjustmentRequest(
            delta=0,
            reason_code=AICreditAdjustmentReason.SUPPORT_CREDIT,
        )

    with pytest.raises(ValidationError):
        AdminAICreditAdjustmentRequest(
            delta=1001,
            reason_code=AICreditAdjustmentReason.SUPPORT_CREDIT,
        )

    with pytest.raises(ValidationError):
        AdminAICreditAdjustmentRequest.model_validate(
            {
                "delta": 1,
                "reason_code": "support_credit",
                "business_id": "attacker-controlled",
            }
        )


def test_admin_adjustment_route_requires_idempotency_header() -> None:
    operation = create_app().openapi()["paths"][
        "/api/v1/admin/businesses/{business_id}/ai-credits/adjustments"
    ]["post"]

    headers = [parameter for parameter in operation["parameters"] if parameter["in"] == "header"]

    key = next(item for item in headers if item["name"].lower() == "idempotency-key")

    assert key["required"] is True
