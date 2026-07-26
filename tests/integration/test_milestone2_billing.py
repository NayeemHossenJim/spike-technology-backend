from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlmodel import func, select

from app.api.v1.billing import get_stripe_gateway
from app.core.config import Settings, get_settings
from app.models.base import utc_now
from app.models.billing import (
    BillingCheckoutSession,
    BillingCheckoutStatus,
    BillingInvoice,
    StripeWebhookEvent,
)
from app.models.subscription import (
    EntitlementSource,
    Subscription,
    SubscriptionEntitlement,
    SubscriptionStatus,
)
from app.services.billing import (
    STRIPE_METADATA_BUSINESS_ID,
    STRIPE_METADATA_CHECKOUT_ID,
    STRIPE_METADATA_ENVIRONMENT,
    STRIPE_METADATA_PLAN_CODE,
    STRIPE_METADATA_SUBSCRIPTION_ID,
)
from app.services.stripe_gateway import StripeObject
from tests.conftest import InMemoryEmailSender
from tests.integration.test_milestone1_foundation import (
    bearer,
    register_verify_and_login,
)


@dataclass
class FakeStripeGateway:
    checkout_calls: list[tuple[StripeObject, str]] = field(default_factory=list)
    price_calls: list[str] = field(default_factory=list)
    price_overrides: dict[str, StripeObject] = field(default_factory=dict)
    update_calls: list[tuple[str, bool, str]] = field(default_factory=list)
    retrieve_calls: list[str] = field(default_factory=list)
    subscriptions: dict[str, StripeObject] = field(default_factory=dict)
    events_by_payload: dict[bytes, StripeObject] = field(default_factory=dict)
    event: StripeObject | None = None

    async def retrieve_price(
        self,
        stripe_price_id: str,
    ) -> StripeObject:
        self.price_calls.append(stripe_price_id)
        if stripe_price_id in self.price_overrides:
            return deepcopy(self.price_overrides[stripe_price_id])
        amounts = {
            "price_premium_monthly": 5999,
            "price_pro_monthly": 9999,
        }
        return {
            "id": stripe_price_id,
            "object": "price",
            "active": True,
            "type": "recurring",
            "billing_scheme": "per_unit",
            "currency": "usd",
            "unit_amount": amounts[stripe_price_id],
            "recurring": {
                "interval": "month",
                "interval_count": 1,
                "usage_type": "licensed",
            },
        }

    async def create_checkout_session(
        self,
        *,
        params: StripeObject,
        idempotency_key: str,
    ) -> StripeObject:
        self.checkout_calls.append((deepcopy(params), idempotency_key))
        sequence = len(self.checkout_calls)
        return {
            "id": f"cs_test_{sequence}",
            "mode": "subscription",
            "status": "open",
            "url": f"https://checkout.stripe.test/session/{sequence}",
            "expires_at": params["expires_at"],
        }

    async def update_subscription(
        self,
        stripe_subscription_id: str,
        *,
        cancel_at_period_end: bool,
        idempotency_key: str,
    ) -> StripeObject:
        self.update_calls.append((stripe_subscription_id, cancel_at_period_end, idempotency_key))
        snapshot = deepcopy(self.subscriptions[stripe_subscription_id])
        snapshot["cancel_at_period_end"] = cancel_at_period_end
        self.subscriptions[stripe_subscription_id] = deepcopy(snapshot)
        return snapshot

    async def retrieve_subscription(
        self,
        stripe_subscription_id: str,
    ) -> StripeObject:
        self.retrieve_calls.append(stripe_subscription_id)
        return deepcopy(self.subscriptions[stripe_subscription_id])

    def construct_webhook_event(
        self,
        *,
        payload: bytes,
        signature: str,
    ) -> StripeObject:
        assert payload
        assert signature == "test-signature"
        event = self.events_by_payload.get(payload, self.event)
        assert event is not None
        return deepcopy(event)


def enabled_stripe_settings() -> Settings:
    current = get_settings()
    return current.model_copy(
        update={
            "stripe_enabled": True,
            "stripe_secret_key": SecretStr("sk_test_integration"),
            "stripe_webhook_secret": SecretStr("whsec_integration"),
            "stripe_premium_monthly_price_id": "price_premium_monthly",
            "stripe_pro_monthly_price_id": "price_pro_monthly",
        }
    )


def configure_stripe_app(app, gateway: FakeStripeGateway) -> Settings:
    settings = enabled_stripe_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_stripe_gateway] = lambda: gateway
    return settings


def stripe_event(
    *,
    event_id: str,
    event_type: str,
    stripe_object: StripeObject,
    created: int,
    livemode: bool = False,
) -> StripeObject:
    return {
        "id": event_id,
        "type": event_type,
        "created": created,
        "livemode": livemode,
        "data": {"object": stripe_object},
    }


def subscription_snapshot(
    *,
    metadata: dict[str, str],
    status: str,
    trial_start: int | None,
    trial_end: int | None,
    period_start: int,
    period_end: int,
    cancel_at_period_end: bool = False,
) -> StripeObject:
    return {
        "id": "sub_test_paid",
        "object": "subscription",
        "customer": "cus_test_owner",
        "status": status,
        "metadata": metadata,
        "trial_start": trial_start,
        "trial_end": trial_end,
        "cancel_at_period_end": cancel_at_period_end,
        "canceled_at": None,
        "ended_at": None,
        "items": {
            "data": [
                {
                    "id": "si_test_paid",
                    "price": {"id": "price_premium_monthly"},
                    "current_period_start": period_start,
                    "current_period_end": period_end,
                }
            ]
        },
    }


@pytest.mark.integration
async def test_checkout_is_idempotent_and_does_not_grant_before_webhook(
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeStripeGateway()
    configure_stripe_app(app, gateway)
    token = await register_verify_and_login(
        client,
        email_sender,
        email="stripe-checkout@example.com",
    )
    onboarding = await client.post(
        "/api/v1/businesses",
        headers=bearer(token),
        json={"name": "Stripe Checkout Tenant"},
    )
    assert onboarding.status_code == 201
    onboarding_payload = onboarding.json()

    missing_key = await client.post(
        "/api/v1/billing/checkout-sessions",
        headers=bearer(token),
        json={"plan_code": "premium"},
    )
    assert missing_key.status_code == 422

    gateway.price_overrides["price_premium_monthly"] = {
        "id": "price_premium_monthly",
        "object": "price",
        "active": True,
        "type": "recurring",
        "billing_scheme": "per_unit",
        "currency": "usd",
        "unit_amount": 1,
        "recurring": {
            "interval": "month",
            "interval_count": 1,
            "usage_type": "licensed",
        },
    }
    misconfigured_price = await client.post(
        "/api/v1/billing/checkout-sessions",
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
        json={"plan_code": "premium"},
    )
    assert misconfigured_price.status_code == 503
    assert gateway.checkout_calls == []
    del gateway.price_overrides["price_premium_monthly"]

    headers = {**bearer(token), "Idempotency-Key": str(uuid4())}
    created = await client.post(
        "/api/v1/billing/checkout-sessions",
        headers=headers,
        json={"plan_code": "premium"},
    )
    retried = await client.post(
        "/api/v1/billing/checkout-sessions",
        headers=headers,
        json={"plan_code": "premium"},
    )
    assert created.status_code == retried.status_code == 201
    assert created.json() == retried.json()
    assert created.json()["status"] == "open"
    assert len(gateway.checkout_calls) == 1
    assert gateway.price_calls == [
        "price_premium_monthly",
        "price_premium_monthly",
        "price_premium_monthly",
    ]

    params, outbound_idempotency_key = gateway.checkout_calls[0]
    assert outbound_idempotency_key.startswith("spike_checkout_")
    assert params["line_items"] == [{"price": "price_premium_monthly", "quantity": 1}]
    assert params["subscription_data"]["trial_end"] == int(
        datetime.fromisoformat(onboarding_payload["subscription"]["trial_ends_at"]).timestamp()
    )
    metadata = params["subscription_data"]["metadata"]
    assert set(metadata) == {
        STRIPE_METADATA_BUSINESS_ID,
        STRIPE_METADATA_SUBSCRIPTION_ID,
        STRIPE_METADATA_PLAN_CODE,
        STRIPE_METADATA_CHECKOUT_ID,
        STRIPE_METADATA_ENVIRONMENT,
    }
    assert metadata[STRIPE_METADATA_PLAN_CODE] == "premium"

    second_attempt = await client.post(
        "/api/v1/billing/checkout-sessions",
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
        json={"plan_code": "pro"},
    )
    assert second_attempt.status_code == 409

    enterprise = await client.post(
        "/api/v1/billing/checkout-sessions",
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
        json={"plan_code": "enterprise"},
    )
    assert enterprise.status_code == 422

    entitlements = await client.get(
        "/api/v1/entitlements/me",
        headers=bearer(token),
    )
    assert entitlements.json()["entitlements"] == [
        {
            "key": "ai_full_responses",
            "is_enabled": True,
            "limit_value": 15,
            "source": "trial",
        }
    ]

    async with session_factory() as session:
        subscription = (await session.execute(select(Subscription))).scalar_one()
        checkout = (await session.execute(select(BillingCheckoutSession))).scalar_one()
        assert subscription.plan_id is None
        assert subscription.stripe_subscription_id is None
        assert BillingCheckoutStatus(checkout.status) is BillingCheckoutStatus.OPEN


@pytest.mark.integration
async def test_webhooks_renewals_cancellation_history_and_entitlements_stay_synchronized(
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeStripeGateway()
    configure_stripe_app(app, gateway)
    token = await register_verify_and_login(
        client,
        email_sender,
        email="stripe-lifecycle@example.com",
    )
    onboarding = await client.post(
        "/api/v1/businesses",
        headers=bearer(token),
        json={"name": "Stripe Lifecycle Tenant"},
    )
    payload = onboarding.json()
    trial_start = int(utc_now().timestamp())
    trial_end = int((utc_now() + timedelta(days=14)).timestamp())

    checkout_response = await client.post(
        "/api/v1/billing/checkout-sessions",
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
        json={"plan_code": "premium"},
    )
    assert checkout_response.status_code == 201
    checkout_id = checkout_response.json()["id"]
    checkout_params = gateway.checkout_calls[0][0]
    metadata = checkout_params["subscription_data"]["metadata"]
    assert metadata[STRIPE_METADATA_CHECKOUT_ID] == checkout_id

    completed_object = {
        "id": "cs_test_1",
        "object": "checkout.session",
        "customer": "cus_test_owner",
        "subscription": "sub_test_paid",
        "metadata": metadata,
    }
    gateway.event = stripe_event(
        event_id="evt_checkout_complete",
        event_type="checkout.session.completed",
        stripe_object=completed_object,
        created=trial_start,
    )
    completed = await client.post(
        "/api/v1/billing/webhooks/stripe",
        headers={"Stripe-Signature": "test-signature"},
        content=b"checkout",
    )
    assert completed.status_code == 200
    assert completed.json() == {
        "received": True,
        "duplicate": False,
        "result": "processed",
    }
    blocked_during_webhook_gap = await client.post(
        "/api/v1/billing/checkout-sessions",
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
        json={"plan_code": "pro"},
    )
    assert blocked_during_webhook_gap.status_code == 409

    trial_snapshot = subscription_snapshot(
        metadata=metadata,
        status="trialing",
        trial_start=trial_start,
        trial_end=trial_end,
        period_start=trial_start,
        period_end=trial_end,
    )
    gateway.subscriptions["sub_test_paid"] = trial_snapshot
    gateway.event = stripe_event(
        event_id="evt_subscription_created",
        event_type="customer.subscription.created",
        stripe_object=trial_snapshot,
        created=trial_start + 1,
    )
    first, duplicate = await asyncio.gather(
        client.post(
            "/api/v1/billing/webhooks/stripe",
            headers={"Stripe-Signature": "test-signature"},
            content=b"subscription-created",
        ),
        client.post(
            "/api/v1/billing/webhooks/stripe",
            headers={"Stripe-Signature": "test-signature"},
            content=b"subscription-created",
        ),
    )
    assert first.status_code == duplicate.status_code == 200
    assert sorted([first.json()["duplicate"], duplicate.json()["duplicate"]]) == [False, True]

    trial_entitlements = await client.get(
        "/api/v1/entitlements/me",
        headers=bearer(token),
    )
    assert trial_entitlements.status_code == 200
    assert trial_entitlements.json()["access_active"] is True
    assert trial_entitlements.json()["entitlements"] == [
        {
            "key": "ai_full_responses",
            "is_enabled": True,
            "limit_value": 15,
            "source": "trial",
        },
        {
            "key": "dashboards",
            "is_enabled": True,
            "limit_value": 20,
            "source": "plan",
        },
    ]

    paid_start = trial_end
    paid_end = int((utc_now() + timedelta(days=44)).timestamp())
    active_snapshot = subscription_snapshot(
        metadata=metadata,
        status="active",
        trial_start=trial_start,
        trial_end=None,
        period_start=paid_start,
        period_end=paid_end,
    )
    gateway.subscriptions["sub_test_paid"] = active_snapshot
    invoice = {
        "id": "in_test_renewal",
        "object": "invoice",
        "customer": "cus_test_owner",
        "parent": {
            "subscription_details": {
                "subscription": "sub_test_paid",
            }
        },
        "number": "INV-0001",
        "status": "paid",
        "currency": "usd",
        "subtotal": 5999,
        "total": 5999,
        "amount_due": 5999,
        "amount_paid": 5999,
        "amount_remaining": 0,
        "created": paid_start,
        "period_start": paid_start,
        "period_end": paid_end,
        "status_transitions": {"paid_at": paid_start + 1},
        "hosted_invoice_url": "https://invoice.stripe.test/in_test_renewal",
        "invoice_pdf": "https://invoice.stripe.test/in_test_renewal.pdf",
    }
    gateway.event = stripe_event(
        event_id="evt_invoice_paid",
        event_type="invoice.paid",
        stripe_object=invoice,
        created=paid_start + 1,
    )
    paid = await client.post(
        "/api/v1/billing/webhooks/stripe",
        headers={"Stripe-Signature": "test-signature"},
        content=b"invoice-paid",
    )
    assert paid.status_code == 200
    assert paid.json()["result"] == "processed"

    stale_open_invoice = deepcopy(invoice)
    stale_open_invoice["status"] = "open"
    stale_open_invoice["amount_paid"] = 0
    stale_open_invoice["amount_remaining"] = 5999
    stale_open_invoice["status_transitions"] = {"paid_at": None}
    gateway.event = stripe_event(
        event_id="evt_invoice_stale_open",
        event_type="invoice.updated",
        stripe_object=stale_open_invoice,
        created=paid_start + 1,
    )
    stale = await client.post(
        "/api/v1/billing/webhooks/stripe",
        headers={"Stripe-Signature": "test-signature"},
        content=b"stale-open-invoice",
    )
    assert stale.status_code == 200
    assert stale.json()["result"] == "ignored"

    history = await client.get(
        "/api/v1/billing/history",
        headers=bearer(token),
    )
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0] == {
        "id": history.json()["items"][0]["id"],
        "subscription_id": payload["subscription"]["id"],
        "invoice_number": "INV-0001",
        "status": "paid",
        "currency": "USD",
        "subtotal_cents": 5999,
        "total_cents": 5999,
        "amount_due_cents": 5999,
        "amount_paid_cents": 5999,
        "amount_remaining_cents": 0,
        "stripe_created_at": history.json()["items"][0]["stripe_created_at"],
        "period_started_at": history.json()["items"][0]["period_started_at"],
        "period_ends_at": history.json()["items"][0]["period_ends_at"],
        "paid_at": history.json()["items"][0]["paid_at"],
        "hosted_invoice_url": "https://invoice.stripe.test/in_test_renewal",
        "invoice_pdf_url": "https://invoice.stripe.test/in_test_renewal.pdf",
    }

    paid_entitlements = await client.get(
        "/api/v1/entitlements/me",
        headers=bearer(token),
    )
    assert paid_entitlements.json()["subscription_status"] == "active"
    assert paid_entitlements.json()["entitlements"] == [
        {
            "key": "ai_full_responses",
            "is_enabled": True,
            "limit_value": 15,
            "source": "plan",
        },
        {
            "key": "dashboards",
            "is_enabled": True,
            "limit_value": 20,
            "source": "plan",
        },
    ]

    concurrent_open = deepcopy(invoice)
    concurrent_open.update(
        {
            "id": "in_test_concurrent",
            "number": "INV-0002",
            "status": "open",
            "amount_paid": 0,
            "amount_remaining": 5999,
            "created": paid_start + 100,
            "status_transitions": {"paid_at": None},
        }
    )
    concurrent_paid = deepcopy(concurrent_open)
    concurrent_paid.update(
        {
            "status": "paid",
            "amount_paid": 5999,
            "amount_remaining": 0,
            "status_transitions": {"paid_at": paid_start + 101},
        }
    )
    gateway.events_by_payload = {
        b"invoice-open-race": stripe_event(
            event_id="evt_invoice_open_race",
            event_type="invoice.finalized",
            stripe_object=concurrent_open,
            created=paid_start + 2,
        ),
        b"invoice-paid-race": stripe_event(
            event_id="evt_invoice_paid_race",
            event_type="invoice.paid",
            stripe_object=concurrent_paid,
            created=paid_start + 3,
        ),
    }
    raced = await asyncio.gather(
        client.post(
            "/api/v1/billing/webhooks/stripe",
            headers={"Stripe-Signature": "test-signature"},
            content=b"invoice-open-race",
        ),
        client.post(
            "/api/v1/billing/webhooks/stripe",
            headers={"Stripe-Signature": "test-signature"},
            content=b"invoice-paid-race",
        ),
    )
    assert [response.status_code for response in raced] == [200, 200]
    gateway.events_by_payload.clear()

    canceled = await client.post(
        "/api/v1/billing/subscription/cancel",
        headers=bearer(token),
    )
    assert canceled.status_code == 200
    assert canceled.json()["cancel_at_period_end"] is True
    resumed = await client.post(
        "/api/v1/billing/subscription/resume",
        headers=bearer(token),
    )
    assert resumed.status_code == 200
    assert resumed.json()["cancel_at_period_end"] is False
    assert [call[1] for call in gateway.update_calls] == [True, False]

    async with session_factory() as session:
        subscription = (
            await session.execute(
                select(Subscription).where(Subscription.stripe_subscription_id == "sub_test_paid")
            )
        ).scalar_one()
        trial_grants = await session.scalar(
            select(func.count())
            .select_from(SubscriptionEntitlement)
            .where(
                SubscriptionEntitlement.subscription_id == subscription.id,
                SubscriptionEntitlement.source == EntitlementSource.TRIAL,
            )
        )
        assert subscription.status == SubscriptionStatus.ACTIVE
        assert subscription.stripe_price_id == "price_premium_monthly"
        assert trial_grants == 0
        concurrent_invoice = (
            await session.execute(
                select(BillingInvoice).where(
                    BillingInvoice.stripe_invoice_id == "in_test_concurrent"
                )
            )
        ).scalar_one()
        assert concurrent_invoice.status == "paid"
        assert concurrent_invoice.amount_paid_cents == 5999
        assert await session.scalar(select(func.count()).select_from(StripeWebhookEvent)) == 6

    past_due_snapshot = deepcopy(active_snapshot)
    past_due_snapshot["status"] = "past_due"
    gateway.subscriptions["sub_test_paid"] = past_due_snapshot
    failed_invoice = deepcopy(invoice)
    failed_invoice.update(
        {
            "id": "in_test_failed",
            "number": "INV-0003",
            "status": "open",
            "amount_paid": 0,
            "amount_remaining": 5999,
            "created": paid_start + 200,
            "status_transitions": {"paid_at": None},
        }
    )
    gateway.event = stripe_event(
        event_id="evt_invoice_payment_failed",
        event_type="invoice.payment_failed",
        stripe_object=failed_invoice,
        created=paid_start + 4,
    )
    payment_failed = await client.post(
        "/api/v1/billing/webhooks/stripe",
        headers={"Stripe-Signature": "test-signature"},
        content=b"invoice-payment-failed",
    )
    assert payment_failed.status_code == 200
    past_due_entitlements = await client.get(
        "/api/v1/entitlements/me",
        headers=bearer(token),
    )
    assert past_due_entitlements.json()["subscription_status"] == "past_due"
    assert past_due_entitlements.json()["access_active"] is False

    unknown_price_snapshot = deepcopy(active_snapshot)
    unknown_price_snapshot["items"]["data"][0]["price"]["id"] = "price_unmapped"
    gateway.subscriptions["sub_test_paid"] = unknown_price_snapshot
    gateway.event = stripe_event(
        event_id="evt_subscription_unknown_price",
        event_type="customer.subscription.updated",
        stripe_object=unknown_price_snapshot,
        created=paid_start + 5,
    )
    fail_closed = await client.post(
        "/api/v1/billing/webhooks/stripe",
        headers={"Stripe-Signature": "test-signature"},
        content=b"unknown-price",
    )
    assert fail_closed.status_code == 200
    fail_closed_entitlements = await client.get(
        "/api/v1/entitlements/me",
        headers=bearer(token),
    )
    assert fail_closed_entitlements.json()["subscription_status"] == "incomplete"
    assert fail_closed_entitlements.json()["access_active"] is False
    assert fail_closed_entitlements.json()["entitlements"] == []

    async with session_factory() as session:
        subscription = (
            await session.execute(
                select(Subscription).where(Subscription.stripe_subscription_id == "sub_test_paid")
            )
        ).scalar_one()
        assert subscription.plan_id is None
        assert subscription.stripe_price_id == "price_unmapped"
        assert await session.scalar(select(func.count()).select_from(StripeWebhookEvent)) == 8


@pytest.mark.integration
async def test_webhook_environment_and_billing_tenant_constraints_fail_closed(
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    test_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeStripeGateway()
    configure_stripe_app(app, gateway)
    token_a = await register_verify_and_login(
        client,
        email_sender,
        email="billing-tenant-a@example.com",
    )
    token_b = await register_verify_and_login(
        client,
        email_sender,
        email="billing-tenant-b@example.com",
    )
    tenant_a = (
        await client.post(
            "/api/v1/businesses",
            headers=bearer(token_a),
            json={"name": "Billing Tenant A"},
        )
    ).json()
    tenant_b = (
        await client.post(
            "/api/v1/businesses",
            headers=bearer(token_b),
            json={"name": "Billing Tenant B"},
        )
    ).json()

    gateway.event = stripe_event(
        event_id="evt_wrong_mode",
        event_type="unhandled.event",
        stripe_object={"id": "obj_wrong_mode"},
        created=int(utc_now().timestamp()),
        livemode=True,
    )
    wrong_mode = await client.post(
        "/api/v1/billing/webhooks/stripe",
        headers={"Stripe-Signature": "test-signature"},
        content=b"wrong-mode",
    )
    assert wrong_mode.status_code == 400

    missing_signature = await client.post(
        "/api/v1/billing/webhooks/stripe",
        content=b"missing-signature",
    )
    assert missing_signature.status_code == 400

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(StripeWebhookEvent)) == 0

    now = utc_now()
    async with test_engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(IntegrityError):
            await connection.execute(
                insert(BillingInvoice).values(
                    id=uuid4(),
                    created_at=now,
                    updated_at=now,
                    business_id=UUID(tenant_b["business"]["id"]),
                    subscription_id=UUID(tenant_a["subscription"]["id"]),
                    stripe_invoice_id="in_cross_tenant",
                    stripe_customer_id="cus_cross_tenant",
                    invoice_number=None,
                    status="paid",
                    currency="USD",
                    subtotal_cents=100,
                    total_cents=100,
                    amount_due_cents=100,
                    amount_paid_cents=100,
                    amount_remaining_cents=0,
                    stripe_created_at=now,
                    period_started_at=now,
                    period_ends_at=now + timedelta(days=30),
                    paid_at=now,
                    hosted_invoice_url=None,
                    invoice_pdf_url=None,
                    last_stripe_event_created_at=now,
                    last_stripe_event_id="evt_cross_tenant",
                )
            )

    own_history = await client.get(
        "/api/v1/billing/history",
        headers=bearer(token_a),
    )
    other_history = await client.get(
        "/api/v1/billing/history",
        headers=bearer(token_b),
    )
    assert own_history.json()["total"] == other_history.json()["total"] == 0
