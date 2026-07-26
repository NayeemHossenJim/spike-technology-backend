from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.main import create_app
from app.models.base import utc_now
from app.models.subscription import (
    AI_FULL_RESPONSES_PER_PERIOD,
    PREMIUM_DASHBOARD_LIMIT,
    TRIAL_DAYS,
    Plan,
    Subscription,
    SubscriptionStatus,
)
from app.schemas.business import BusinessCreate
from app.services.subscriptions import SubscriptionService
from app.services.tenant import tenant_select


def test_confirmed_subscription_limits_are_locked() -> None:
    assert TRIAL_DAYS == 14
    assert AI_FULL_RESPONSES_PER_PERIOD == 15
    assert PREMIUM_DASHBOARD_LIMIT == 20


def test_business_create_strips_name_and_forbids_client_tenant_ids() -> None:
    payload = BusinessCreate(name="  Acme Analytics  ", industry="Technology")
    assert payload.name == "Acme Analytics"
    assert payload.industry == "Technology"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        BusinessCreate.model_validate(
            {
                "name": "Acme Analytics",
                "business_id": str(uuid4()),
            }
        )


@pytest.mark.parametrize("name", ["", " ", "A", "x" * 161])
def test_business_create_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValidationError):
        BusinessCreate(name=name)


def test_tenant_select_refuses_global_models() -> None:
    with pytest.raises(TypeError, match="Plan is not tenant-scoped"):
        tenant_select(Plan, uuid4())


def test_tenant_select_always_adds_business_predicate() -> None:
    business_id = uuid4()
    statement = tenant_select(Subscription, business_id)
    compiled = str(statement)
    assert "subscriptions.business_id" in compiled
    assert business_id in statement.compile().params.values()


def test_trial_access_is_active_only_inside_confirmed_window() -> None:
    now = utc_now()
    subscription = Subscription(
        business_id=uuid4(),
        status=SubscriptionStatus.TRIALING,
        trial_started_at=now - timedelta(days=1),
        trial_ends_at=now + timedelta(days=13),
        current_period_started_at=now - timedelta(days=1),
        current_period_ends_at=now + timedelta(days=13),
    )

    active = SubscriptionService.evaluate_access(subscription, now=now)
    expired = SubscriptionService.evaluate_access(
        subscription,
        now=now + timedelta(days=14),
    )

    assert active.active is True
    assert active.reason == "active"
    assert expired.active is False
    assert expired.reason == "trial_expired"


def test_active_subscription_fails_closed_without_billing_period() -> None:
    subscription = Subscription(
        business_id=uuid4(),
        status=SubscriptionStatus.ACTIVE,
    )
    decision = SubscriptionService.evaluate_access(subscription)
    assert decision.active is False
    assert decision.reason == "billing_period_not_configured"


def test_milestone1_openapi_contract_has_server_derived_tenancy() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]

    assert "/api/v1/businesses" in paths
    assert "/api/v1/businesses/me" in paths
    assert "/api/v1/plans" in paths
    assert "/api/v1/subscriptions/me" in paths
    assert "/api/v1/subscriptions/{subscription_id}" in paths
    assert "/api/v1/entitlements/me" in paths

    create_properties = schema["components"]["schemas"]["BusinessCreate"]["properties"]
    assert list(create_properties) == ["name", "industry"]
    assert "business_id" not in create_properties
