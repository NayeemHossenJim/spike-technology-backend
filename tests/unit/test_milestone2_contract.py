from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import timedelta
from uuid import uuid4

import pytest
import stripe
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.models.base import utc_now
from app.models.billing import BillingCheckoutSession
from app.models.subscription import Plan, SeededPlanCode, Subscription, SubscriptionStatus
from app.schemas.billing import CheckoutSessionCreate
from app.services.billing import (
    STRIPE_METADATA_BUSINESS_ID,
    STRIPE_METADATA_CHECKOUT_ID,
    STRIPE_METADATA_ENVIRONMENT,
    STRIPE_METADATA_PLAN_CODE,
    STRIPE_METADATA_SUBSCRIPTION_ID,
    BillingPriceConfigurationError,
    _invoice_subscription_id,
    _subscription_item_details,
    build_checkout_params,
    configured_plan_code_for_price,
    configured_price_id,
    validate_checkout_price,
)
from app.services.stripe_gateway import (
    StripeCheckoutRejectedError,
    StripeGatewayError,
    StripeSdkGateway,
    StripeWebhookSignatureError,
)


def stripe_settings(**overrides) -> Settings:
    values = {
        "app_env": "test",
        "database_url": "postgresql+asyncpg://spike:spike@localhost/spike_test",
        "redis_url": "redis://localhost:6379/15",
        "celery_broker_url": "redis://localhost:6379/15",
        "celery_result_backend": "redis://localhost:6379/14",
        "jwt_secret_key": "test-only-secret-with-at-least-thirty-two-characters",
        "email_backend": "console",
        "stripe_enabled": True,
        "stripe_secret_key": "sk_test_unit",
        "stripe_webhook_secret": "whsec_unit",
        "stripe_premium_monthly_price_id": "price_premium_monthly",
        "stripe_pro_monthly_price_id": "price_pro_monthly",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class RaisingCheckoutStripeClient:
    def __init__(self, error: stripe.StripeError) -> None:
        self.error = error
        self.v1 = self
        self.checkout = self
        self.sessions = self

    async def create_async(self, *_args, **_kwargs):
        raise self.error


def test_stripe_configuration_fails_closed() -> None:
    with pytest.raises(ValidationError, match="required settings are missing"):
        stripe_settings(stripe_secret_key=None)
    with pytest.raises(ValidationError, match="sk_test_"):
        stripe_settings(stripe_secret_key="sk_live_accidental")
    with pytest.raises(ValidationError, match="different Stripe Price IDs"):
        stripe_settings(stripe_pro_monthly_price_id="price_premium_monthly")
    with pytest.raises(ValidationError, match=r"\{CHECKOUT_SESSION_ID\}"):
        stripe_settings(stripe_checkout_success_url="https://example.com/success")


def test_only_approved_self_service_plans_have_price_mappings() -> None:
    settings = stripe_settings()
    assert configured_price_id(settings, SeededPlanCode.PREMIUM) == "price_premium_monthly"
    assert configured_price_id(settings, SeededPlanCode.PRO) == "price_pro_monthly"
    assert configured_price_id(settings, SeededPlanCode.ENTERPRISE) is None
    assert (
        configured_plan_code_for_price(settings, "price_premium_monthly") is SeededPlanCode.PREMIUM
    )
    assert configured_plan_code_for_price(settings, "price_unknown") is None


def test_checkout_price_must_match_the_approved_monthly_plan() -> None:
    plan = Plan(
        code="premium",
        name="Premium",
        monthly_price_cents=5999,
        currency="USD",
    )
    price = {
        "id": "price_premium_monthly",
        "object": "price",
        "active": True,
        "type": "recurring",
        "billing_scheme": "per_unit",
        "currency": "usd",
        "unit_amount": 5999,
        "recurring": {
            "interval": "month",
            "interval_count": 1,
            "usage_type": "licensed",
        },
    }
    validate_checkout_price(
        price,
        expected_price_id="price_premium_monthly",
        plan=plan,
    )

    invalid_price = dict(price, unit_amount=9999)
    with pytest.raises(BillingPriceConfigurationError):
        validate_checkout_price(
            invalid_price,
            expected_price_id="price_premium_monthly",
            plan=plan,
        )


def test_checkout_carries_remaining_onboarding_trial_without_extending_it() -> None:
    settings = stripe_settings()
    now = utc_now()
    business_id = uuid4()
    subscription = Subscription(
        business_id=business_id,
        status=SubscriptionStatus.TRIALING,
        trial_started_at=now - timedelta(days=1),
        trial_ends_at=now + timedelta(days=13),
        current_period_started_at=now - timedelta(days=1),
        current_period_ends_at=now + timedelta(days=13),
    )
    checkout = BillingCheckoutSession(
        business_id=business_id,
        subscription_id=subscription.id,
        plan_id=uuid4(),
        idempotency_key_digest="a" * 64,
    )

    params = build_checkout_params(
        settings=settings,
        checkout=checkout,
        subscription=subscription,
        plan_code=SeededPlanCode.PREMIUM,
        price_id="price_premium_monthly",
        customer_email="owner@example.com",
        stripe_customer_id=None,
        now=now,
    )

    subscription_data = params["subscription_data"]
    assert "payment_settings" not in subscription_data
    assert params["payment_method_collection"] == "always"
    assert subscription_data["trial_end"] == int(subscription.trial_ends_at.timestamp())
    assert "trial_period_days" not in subscription_data
    assert params["customer_email"] == "owner@example.com"
    assert params["expires_at"] == int((now + timedelta(minutes=60)).timestamp())
    assert params["metadata"] == {
        STRIPE_METADATA_BUSINESS_ID: str(business_id),
        STRIPE_METADATA_SUBSCRIPTION_ID: str(subscription.id),
        STRIPE_METADATA_PLAN_CODE: "premium",
        STRIPE_METADATA_CHECKOUT_ID: str(checkout.id),
        STRIPE_METADATA_ENVIRONMENT: "test",
    }
    assert "owner@example.com" not in json.dumps(params["metadata"])


def test_checkout_does_not_create_a_second_trial_near_or_after_expiry() -> None:
    settings = stripe_settings()
    now = utc_now()
    business_id = uuid4()
    subscription = Subscription(
        business_id=business_id,
        status=SubscriptionStatus.TRIALING,
        trial_started_at=now - timedelta(days=14),
        trial_ends_at=now + timedelta(hours=47),
    )
    checkout = BillingCheckoutSession(
        business_id=business_id,
        subscription_id=subscription.id,
        plan_id=uuid4(),
        idempotency_key_digest="b" * 64,
    )
    params = build_checkout_params(
        settings=settings,
        checkout=checkout,
        subscription=subscription,
        plan_code=SeededPlanCode.PRO,
        price_id="price_pro_monthly",
        customer_email="owner@example.com",
        stripe_customer_id="cus_existing",
        now=now,
    )
    assert "trial_end" not in params["subscription_data"]
    assert "trial_period_days" not in params["subscription_data"]
    assert params["customer"] == "cus_existing"
    assert "customer_email" not in params


async def test_checkout_gateway_marks_invalid_request_as_definitive_rejection() -> None:
    gateway = StripeSdkGateway(stripe_settings())
    gateway._client = RaisingCheckoutStripeClient(  # type: ignore[assignment]
        stripe.InvalidRequestError(
            "Invalid Checkout parameters.",
            "subscription_data[trial_end]",
            http_status=400,
        )
    )

    with pytest.raises(StripeCheckoutRejectedError):
        await gateway.create_checkout_session(
            params={"mode": "subscription"},
            idempotency_key="checkout-definitive",
        )


@pytest.mark.parametrize(
    "error",
    [
        stripe.APIConnectionError("Connection failed."),
        stripe.APIError("Stripe server failed.", http_status=500),
        stripe.IdempotencyError("Idempotency result requires reconciliation."),
        stripe.RateLimitError("Rate limited.", http_status=429),
    ],
    ids=["connection", "api", "idempotency", "rate-limit"],
)
async def test_checkout_gateway_keeps_ambiguous_failures_reconcilable(
    error: stripe.StripeError,
) -> None:
    gateway = StripeSdkGateway(stripe_settings())
    gateway._client = RaisingCheckoutStripeClient(error)  # type: ignore[assignment]

    with pytest.raises(StripeGatewayError) as caught:
        await gateway.create_checkout_session(
            params={"mode": "subscription"},
            idempotency_key="checkout-ambiguous",
        )
    assert not isinstance(caught.value, StripeCheckoutRejectedError)


def test_subscription_period_parser_uses_current_item_level_fields() -> None:
    started = int((utc_now() - timedelta(days=1)).timestamp())
    ends = int((utc_now() + timedelta(days=29)).timestamp())
    price_id, period_started_at, period_ends_at = _subscription_item_details(
        {
            "items": {
                "data": [
                    {
                        "price": {"id": "price_premium_monthly"},
                        "current_period_start": started,
                        "current_period_end": ends,
                    }
                ]
            }
        }
    )
    assert price_id == "price_premium_monthly"
    assert int(period_started_at.timestamp()) == started
    assert int(period_ends_at.timestamp()) == ends


def test_invoice_subscription_parser_supports_current_and_legacy_shapes() -> None:
    assert (
        _invoice_subscription_id(
            {
                "parent": {
                    "subscription_details": {
                        "subscription": "sub_current",
                    }
                }
            }
        )
        == "sub_current"
    )
    assert _invoice_subscription_id({"subscription": "sub_legacy"}) == "sub_legacy"


def test_stripe_webhook_verification_uses_the_exact_raw_payload() -> None:
    settings = stripe_settings()
    payload = json.dumps(
        {
            "id": "evt_unit",
            "type": "test.event",
            "created": int(time.time()),
            "livemode": False,
            "data": {"object": {"id": "obj_unit"}},
        },
        separators=(",", ":"),
    ).encode()
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.{payload.decode()}".encode()
    digest = hmac.new(
        b"whsec_unit",
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    signature = f"t={timestamp},v1={digest}"

    event = StripeSdkGateway(settings).construct_webhook_event(
        payload=payload,
        signature=signature,
    )
    assert event["id"] == "evt_unit"

    with pytest.raises(StripeWebhookSignatureError):
        StripeSdkGateway(settings).construct_webhook_event(
            payload=payload + b" ",
            signature=signature,
        )


def test_checkout_schema_and_openapi_do_not_accept_tenant_or_price_ids() -> None:
    payload = CheckoutSessionCreate(plan_code=SeededPlanCode.PREMIUM)
    assert payload.plan_code is SeededPlanCode.PREMIUM
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CheckoutSessionCreate.model_validate(
            {
                "plan_code": "premium",
                "business_id": str(uuid4()),
                "stripe_price_id": "price_attacker",
            }
        )

    schema = create_app().openapi()
    assert schema["info"]["version"] == "0.3.0"
    paths = schema["paths"]
    assert "/api/v1/billing/checkout-sessions" in paths
    assert "/api/v1/billing/webhooks/stripe" in paths
    assert "/api/v1/billing/subscription/cancel" in paths
    assert "/api/v1/billing/subscription/resume" in paths
    assert "/api/v1/billing/history" in paths
    properties = schema["components"]["schemas"]["CheckoutSessionCreate"]["properties"]
    assert list(properties) == ["plan_code"]
