from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.config import AppEnvironment, Settings
from app.models.base import utc_now
from app.models.billing import (
    BillingCheckoutSession,
    BillingCheckoutStatus,
    BillingInvoice,
    BillingInvoiceStatus,
    StripeWebhookEvent,
    StripeWebhookResult,
)
from app.models.subscription import (
    EntitlementSource,
    Plan,
    SeededPlanCode,
    Subscription,
    SubscriptionEntitlement,
    SubscriptionStatus,
)
from app.models.user import User
from app.services.stripe_gateway import StripeGateway, StripeGatewayError, StripeObject
from app.services.subscriptions import CURRENT_SUBSCRIPTION_STATUSES
from app.services.tenant import TenantScope

# Stripe requires an absolute Checkout trial end to be at least 48 hours away.
# One extra hour prevents timestamp truncation and network latency from crossing
# that boundary while preserving the original local trial end.
STRIPE_MINIMUM_ABSOLUTE_TRIAL_LEAD = timedelta(hours=49)
STRIPE_METADATA_BUSINESS_ID = "spike_business_id"
STRIPE_METADATA_SUBSCRIPTION_ID = "spike_subscription_id"
STRIPE_METADATA_PLAN_CODE = "spike_plan_code"
STRIPE_METADATA_CHECKOUT_ID = "spike_checkout_id"
STRIPE_METADATA_ENVIRONMENT = "spike_environment"
APP_SUBSCRIPTION_METADATA_KEYS = {
    STRIPE_METADATA_BUSINESS_ID,
    STRIPE_METADATA_SUBSCRIPTION_ID,
    STRIPE_METADATA_PLAN_CODE,
    STRIPE_METADATA_CHECKOUT_ID,
    STRIPE_METADATA_ENVIRONMENT,
}

SUBSCRIPTION_EVENTS = {
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.paused",
    "customer.subscription.resumed",
}
CHECKOUT_COMPLETE_EVENTS = {
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
}
INVOICE_EVENTS = {
    "invoice.created",
    "invoice.updated",
    "invoice.finalized",
    "invoice.paid",
    "invoice.payment_failed",
    "invoice.payment_action_required",
    "invoice.voided",
    "invoice.marked_uncollectible",
}
INVOICE_STATUS_RANK = {
    BillingInvoiceStatus.DRAFT: 0,
    BillingInvoiceStatus.OPEN: 1,
    BillingInvoiceStatus.PAID: 2,
    BillingInvoiceStatus.UNCOLLECTIBLE: 2,
    BillingInvoiceStatus.VOID: 2,
}


class BillingNotConfiguredError(Exception):
    pass


class BillingPlanNotFoundError(Exception):
    pass


class BillingPlanNotCheckoutEligibleError(Exception):
    pass


class BillingPriceConfigurationError(Exception):
    pass


class BillingCheckoutConflictError(Exception):
    pass


class BillingCheckoutInProgressError(Exception):
    def __init__(self, checkout: BillingCheckoutSession) -> None:
        super().__init__("A Checkout Session is already in progress.")
        self.checkout = checkout


class StripeSubscriptionRequiredError(Exception):
    pass


class StripeWebhookDataError(Exception):
    pass


class StripeWebhookEnvironmentError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CheckoutResult:
    checkout: BillingCheckoutSession
    plan_code: SeededPlanCode


@dataclass(frozen=True, slots=True)
class BillingHistoryPage:
    items: tuple[BillingInvoice, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class WebhookProcessResult:
    duplicate: bool
    result: StripeWebhookResult


def configured_price_id(settings: Settings, plan_code: SeededPlanCode) -> str | None:
    if plan_code is SeededPlanCode.PREMIUM:
        return settings.stripe_premium_monthly_price_id
    if plan_code is SeededPlanCode.PRO:
        return settings.stripe_pro_monthly_price_id
    return None


def configured_plan_code_for_price(
    settings: Settings,
    stripe_price_id: str,
) -> SeededPlanCode | None:
    for plan_code in (SeededPlanCode.PREMIUM, SeededPlanCode.PRO):
        if configured_price_id(settings, plan_code) == stripe_price_id:
            return plan_code
    return None


def validate_checkout_price(
    remote: StripeObject,
    *,
    expected_price_id: str,
    plan: Plan,
) -> None:
    recurring = remote.get("recurring")
    valid = (
        remote.get("id") == expected_price_id
        and remote.get("object") == "price"
        and remote.get("active") is True
        and remote.get("type") == "recurring"
        and remote.get("billing_scheme") == "per_unit"
        and isinstance(remote.get("currency"), str)
        and remote["currency"].upper() == plan.currency.upper()
        and isinstance(remote.get("unit_amount"), int)
        and not isinstance(remote.get("unit_amount"), bool)
        and remote["unit_amount"] == plan.monthly_price_cents
        and isinstance(recurring, dict)
        and recurring.get("interval") == "month"
        and recurring.get("interval_count") == 1
        and recurring.get("usage_type") == "licensed"
    )
    if not valid:
        raise BillingPriceConfigurationError(
            "The configured Stripe Price does not match the approved local Plan."
        )


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise StripeWebhookDataError(f"Stripe object is missing {key}.")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        object_id = value.get("id")
        if isinstance(object_id, str) and object_id:
            return object_id
    raise StripeWebhookDataError("Stripe returned an invalid object reference.")


def _stripe_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StripeWebhookDataError("Stripe returned an invalid timestamp.")
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise StripeWebhookDataError("Stripe returned an invalid timestamp.") from exc


def _stripe_integer(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise StripeWebhookDataError(f"Stripe object is missing {key}.")
    return value


def _metadata(data: dict[str, Any]) -> dict[str, str]:
    raw_metadata = data.get("metadata")
    if raw_metadata is None:
        return {}
    if not isinstance(raw_metadata, dict):
        raise StripeWebhookDataError("Stripe metadata is invalid.")
    return {str(key): str(value) for key, value in raw_metadata.items() if value is not None}


def _metadata_uuid(metadata: dict[str, str], key: str) -> UUID:
    value = metadata.get(key)
    if value is None:
        raise StripeWebhookDataError(f"Stripe metadata {key} is missing.")
    try:
        return UUID(value)
    except ValueError as exc:
        raise StripeWebhookDataError(f"Stripe metadata {key} is invalid.") from exc


def _subscription_item_details(
    snapshot: dict[str, Any],
) -> tuple[str, datetime | None, datetime | None]:
    items = snapshot.get("items")
    item_data = items.get("data") if isinstance(items, dict) else None
    if not isinstance(item_data, list) or len(item_data) != 1:
        raise StripeWebhookDataError("Exactly one Stripe subscription item is required.")
    item = item_data[0]
    if not isinstance(item, dict):
        raise StripeWebhookDataError("Stripe subscription item is invalid.")

    price_id = _optional_string(item.get("price"))
    if price_id is None:
        raise StripeWebhookDataError("Stripe subscription item has no Price.")

    period_started_at = _stripe_datetime(
        item.get("current_period_start", snapshot.get("current_period_start"))
    )
    period_ends_at = _stripe_datetime(
        item.get("current_period_end", snapshot.get("current_period_end"))
    )
    return price_id, period_started_at, period_ends_at


def _invoice_subscription_id(invoice: dict[str, Any]) -> str | None:
    legacy = _optional_string(invoice.get("subscription"))
    if legacy is not None:
        return legacy
    parent = invoice.get("parent")
    details = parent.get("subscription_details") if isinstance(parent, dict) else None
    return _optional_string(details.get("subscription")) if isinstance(details, dict) else None


def _remaining_trial_end(
    subscription: Subscription,
    *,
    now: datetime,
) -> int | None:
    if (
        subscription.status != SubscriptionStatus.TRIALING
        or subscription.stripe_subscription_id is not None
        or subscription.trial_ends_at is None
        or subscription.trial_ends_at < now + STRIPE_MINIMUM_ABSOLUTE_TRIAL_LEAD
    ):
        return None
    return int(subscription.trial_ends_at.timestamp())


def build_checkout_params(
    *,
    settings: Settings,
    checkout: BillingCheckoutSession,
    subscription: Subscription,
    plan_code: SeededPlanCode,
    price_id: str,
    customer_email: str,
    stripe_customer_id: str | None,
    now: datetime,
) -> StripeObject:
    metadata = {
        STRIPE_METADATA_BUSINESS_ID: str(checkout.business_id),
        STRIPE_METADATA_SUBSCRIPTION_ID: str(checkout.subscription_id),
        STRIPE_METADATA_PLAN_CODE: plan_code.value,
        STRIPE_METADATA_CHECKOUT_ID: str(checkout.id),
        STRIPE_METADATA_ENVIRONMENT: settings.app_env.value,
    }
    subscription_data: StripeObject = {
    "metadata": metadata,
    }
    trial_end = _remaining_trial_end(subscription, now=now)
    if trial_end is not None:
        subscription_data["trial_end"] = trial_end
        subscription_data["trial_settings"] = {"end_behavior": {"missing_payment_method": "cancel"}}

    params: StripeObject = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "client_reference_id": str(checkout.id),
        "metadata": metadata,
        "subscription_data": subscription_data,
        "payment_method_collection": "always",
        "success_url": settings.stripe_checkout_success_url,
        "cancel_url": settings.stripe_checkout_cancel_url,
        "expires_at": int(
            (now + timedelta(minutes=settings.stripe_checkout_session_minutes)).timestamp()
        ),
    }
    if stripe_customer_id is not None:
        params["customer"] = stripe_customer_id
    else:
        params["customer_email"] = customer_email
    return params


class CheckoutService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        gateway: StripeGateway,
    ) -> None:
        self.session = session
        self.settings = settings
        self.gateway = gateway

    async def _plan(self, plan_code: SeededPlanCode) -> Plan:
        result = await self.session.execute(
            select(Plan).where(
                Plan.code == plan_code.value,
                Plan.is_active.is_(True),
                Plan.is_public.is_(True),
            )
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            raise BillingPlanNotFoundError
        if plan.is_custom_pricing or configured_price_id(self.settings, plan_code) is None:
            raise BillingPlanNotCheckoutEligibleError
        return plan

    async def _expire_stale_attempts(self, scope: TenantScope, now: datetime) -> None:
        result = await self.session.execute(
            scope.select(
                BillingCheckoutSession,
                BillingCheckoutSession.status.in_(
                    (BillingCheckoutStatus.PENDING, BillingCheckoutStatus.OPEN)
                ),
                BillingCheckoutSession.expires_at.is_not(None),
                BillingCheckoutSession.expires_at <= now,
            ).with_for_update()
        )
        for checkout in result.scalars().all():
            checkout.status = BillingCheckoutStatus.EXPIRED

    async def _latest_customer_id(self, scope: TenantScope) -> str | None:
        result = await self.session.execute(
            scope.select(
                Subscription,
                Subscription.stripe_customer_id.is_not(None),
            )
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        subscription = result.scalar_one_or_none()
        return subscription.stripe_customer_id if subscription is not None else None

    async def _current_subscription_for_update(
        self,
        scope: TenantScope,
    ) -> Subscription | None:
        result = await self.session.execute(
            scope.select(
                Subscription,
                Subscription.status.in_(CURRENT_SUBSCRIPTION_STATUSES),
            )
            .order_by(Subscription.created_at.desc())
            .with_for_update()
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _prepare_attempt(
        self,
        *,
        scope: TenantScope,
        plan: Plan,
        idempotency_key_digest: str,
        now: datetime,
    ) -> tuple[BillingCheckoutSession, Subscription, str | None]:
        await self._expire_stale_attempts(scope, now)

        existing_result = await self.session.execute(
            scope.select(
                BillingCheckoutSession,
                BillingCheckoutSession.idempotency_key_digest == idempotency_key_digest,
            ).with_for_update()
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            if existing.plan_id != plan.id:
                raise BillingCheckoutConflictError
            subscription = await scope.get(
                self.session,
                Subscription,
                existing.subscription_id,
            )
            if subscription is None:
                raise BillingCheckoutConflictError
            customer_id = await self._latest_customer_id(scope)
            await self.session.commit()
            return existing, subscription, customer_id

        open_result = await self.session.execute(
            scope.select(
                BillingCheckoutSession,
                BillingCheckoutSession.status.in_(
                    (BillingCheckoutStatus.PENDING, BillingCheckoutStatus.OPEN)
                ),
            )
            .order_by(BillingCheckoutSession.created_at.desc())
            .limit(1)
        )
        open_checkout = open_result.scalar_one_or_none()
        if open_checkout is not None:
            raise BillingCheckoutInProgressError(open_checkout)

        subscription = await self._current_subscription_for_update(scope)
        if subscription is not None and subscription.stripe_subscription_id is not None:
            raise BillingCheckoutConflictError
        if subscription is not None:
            completed_result = await self.session.execute(
                scope.select(
                    BillingCheckoutSession,
                    BillingCheckoutSession.subscription_id == subscription.id,
                    BillingCheckoutSession.status == BillingCheckoutStatus.COMPLETE,
                    BillingCheckoutSession.stripe_subscription_id.is_not(None),
                )
                .order_by(BillingCheckoutSession.completed_at.desc())
                .limit(1)
            )
            if completed_result.scalar_one_or_none() is not None:
                # Checkout finished but the authoritative subscription webhook has
                # not attached its state yet. Refuse a second paid subscription.
                raise BillingCheckoutConflictError
        if subscription is None:
            subscription = Subscription(
                business_id=scope.business_id,
                plan_id=None,
                status=SubscriptionStatus.INCOMPLETE,
            )
            self.session.add(subscription)

        customer_id = await self._latest_customer_id(scope)
        checkout = BillingCheckoutSession(
            business_id=scope.business_id,
            subscription_id=subscription.id,
            plan_id=plan.id,
            status=BillingCheckoutStatus.PENDING,
            idempotency_key_digest=idempotency_key_digest,
            expires_at=now + timedelta(minutes=self.settings.stripe_checkout_session_minutes),
        )
        self.session.add(checkout)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            duplicate_result = await self.session.execute(
                scope.select(
                    BillingCheckoutSession,
                    BillingCheckoutSession.idempotency_key_digest == idempotency_key_digest,
                )
            )
            duplicate = duplicate_result.scalar_one_or_none()
            if duplicate is not None and duplicate.plan_id == plan.id:
                duplicate_subscription = await scope.get(
                    self.session,
                    Subscription,
                    duplicate.subscription_id,
                )
                if duplicate_subscription is None:
                    raise BillingCheckoutConflictError from None
                return duplicate, duplicate_subscription, await self._latest_customer_id(scope)
            open_result = await self.session.execute(
                scope.select(
                    BillingCheckoutSession,
                    BillingCheckoutSession.status.in_(
                        (BillingCheckoutStatus.PENDING, BillingCheckoutStatus.OPEN)
                    ),
                )
                .order_by(BillingCheckoutSession.created_at.desc())
                .limit(1)
            )
            open_checkout = open_result.scalar_one_or_none()
            if open_checkout is not None:
                raise BillingCheckoutInProgressError(open_checkout) from None
            raise
        return checkout, subscription, customer_id

    async def create(
        self,
        *,
        scope: TenantScope,
        user: User,
        plan_code: SeededPlanCode,
        idempotency_key: str,
    ) -> CheckoutResult:
        if not self.settings.stripe_enabled:
            raise BillingNotConfiguredError
        plan = await self._plan(plan_code)
        price_id = configured_price_id(self.settings, plan_code)
        if price_id is None:
            raise BillingPlanNotCheckoutEligibleError

        remote_price = await self.gateway.retrieve_price(price_id)
        validate_checkout_price(
            remote_price,
            expected_price_id=price_id,
            plan=plan,
        )

        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        now = utc_now()
        checkout, subscription, customer_id = await self._prepare_attempt(
            scope=scope,
            plan=plan,
            idempotency_key_digest=digest,
            now=now,
        )
        if BillingCheckoutStatus(checkout.status) is not BillingCheckoutStatus.PENDING:
            return CheckoutResult(checkout=checkout, plan_code=plan_code)

        params = build_checkout_params(
            settings=self.settings,
            checkout=checkout,
            subscription=subscription,
            plan_code=plan_code,
            price_id=price_id,
            customer_email=user.email,
            stripe_customer_id=customer_id,
            now=now,
        )
        remote = await self.gateway.create_checkout_session(
            params=params,
            idempotency_key=f"spike_checkout_{checkout.id}",
        )
        remote_id = _required_string(remote, "id")
        checkout_url = _required_string(remote, "url")
        expires_at = _stripe_datetime(remote.get("expires_at"))
        if expires_at is None:
            raise StripeGatewayError("Stripe Checkout did not return an expiry time.")
        if remote.get("mode") not in {None, "subscription"}:
            raise StripeGatewayError("Stripe Checkout returned the wrong mode.")

        locked_result = await self.session.execute(
            scope.select(
                BillingCheckoutSession,
                BillingCheckoutSession.id == checkout.id,
            ).with_for_update()
        )
        locked = locked_result.scalar_one()
        locked.stripe_checkout_session_id = remote_id
        locked.checkout_url = checkout_url
        locked.expires_at = expires_at
        if BillingCheckoutStatus(locked.status) is BillingCheckoutStatus.PENDING:
            locked.status = BillingCheckoutStatus.OPEN
        await self.session.commit()
        return CheckoutResult(checkout=locked, plan_code=plan_code)


class SubscriptionSynchronizer:
    def __init__(self, *, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def _plan_for_price(self, price_id: str) -> tuple[Plan, SeededPlanCode]:
        plan_code = configured_plan_code_for_price(self.settings, price_id)
        if plan_code is None:
            raise StripeWebhookDataError("Stripe subscription uses an unknown Price.")
        result = await self.session.execute(select(Plan).where(Plan.code == plan_code.value))
        plan = result.scalar_one_or_none()
        if plan is None:
            raise StripeWebhookDataError("The Stripe Price has no local Plan.")
        return plan, plan_code

    def _validate_existing_metadata(
        self,
        subscription: Subscription,
        metadata: dict[str, str],
    ) -> None:
        business_value = metadata.get(STRIPE_METADATA_BUSINESS_ID)
        if business_value is not None:
            try:
                metadata_business_id = UUID(business_value)
            except ValueError as exc:
                raise StripeWebhookDataError(
                    "Stripe subscription tenant metadata is invalid."
                ) from exc
            if metadata_business_id != subscription.business_id:
                raise StripeWebhookDataError("Stripe subscription tenant metadata does not match.")
        environment = metadata.get(STRIPE_METADATA_ENVIRONMENT)
        if environment is not None and environment != self.settings.app_env.value:
            raise StripeWebhookEnvironmentError(
                "Stripe subscription belongs to another environment."
            )

    async def _fail_closed(
        self,
        *,
        subscription: Subscription,
        stripe_subscription_id: str,
        stripe_customer_id: str,
        stripe_price_id: str,
        snapshot: dict[str, Any],
        event_id: str | None,
        event_created_at: datetime | None,
    ) -> Subscription:
        subscription.plan_id = None
        subscription.status = SubscriptionStatus.INCOMPLETE
        subscription.stripe_customer_id = stripe_customer_id
        subscription.stripe_subscription_id = stripe_subscription_id
        subscription.stripe_price_id = stripe_price_id
        subscription.trial_ends_at = None
        subscription.current_period_started_at = None
        subscription.current_period_ends_at = None
        subscription.cancel_at_period_end = bool(snapshot.get("cancel_at_period_end", False))
        subscription.canceled_at = _stripe_datetime(snapshot.get("canceled_at"))
        subscription.ended_at = _stripe_datetime(snapshot.get("ended_at"))
        subscription.last_stripe_synced_at = utc_now()
        if (
            event_id is not None
            and event_created_at is not None
            and (
                subscription.last_stripe_event_created_at is None
                or event_created_at >= subscription.last_stripe_event_created_at
            )
        ):
            subscription.last_stripe_event_id = event_id
            subscription.last_stripe_event_created_at = event_created_at
        await self.session.execute(
            delete(SubscriptionEntitlement).where(
                SubscriptionEntitlement.subscription_id == subscription.id,
                SubscriptionEntitlement.business_id == subscription.business_id,
                SubscriptionEntitlement.source == EntitlementSource.TRIAL,
            )
        )
        return subscription

    async def _find_local_subscription(
        self,
        *,
        existing: Subscription | None,
        metadata: dict[str, str],
        plan: Plan,
        plan_code: SeededPlanCode,
    ) -> Subscription | None:
        if existing is not None:
            self._validate_existing_metadata(existing, metadata)
            metadata_plan = metadata.get(STRIPE_METADATA_PLAN_CODE)
            if metadata_plan is not None and metadata_plan != plan_code.value:
                raise StripeWebhookDataError("Stripe subscription Plan metadata does not match.")
            return existing

        if not APP_SUBSCRIPTION_METADATA_KEYS.intersection(metadata):
            return None
        if not APP_SUBSCRIPTION_METADATA_KEYS.issubset(metadata):
            raise StripeWebhookDataError("Stripe subscription metadata is incomplete.")
        if metadata[STRIPE_METADATA_ENVIRONMENT] != self.settings.app_env.value:
            raise StripeWebhookEnvironmentError(
                "Stripe subscription belongs to another environment."
            )
        if metadata[STRIPE_METADATA_PLAN_CODE] != plan_code.value:
            raise StripeWebhookDataError("Stripe subscription Plan metadata does not match.")

        business_id = _metadata_uuid(metadata, STRIPE_METADATA_BUSINESS_ID)
        subscription_id = _metadata_uuid(metadata, STRIPE_METADATA_SUBSCRIPTION_ID)
        checkout_id = _metadata_uuid(metadata, STRIPE_METADATA_CHECKOUT_ID)
        scope = TenantScope(business_id)
        subscription = await scope.get(self.session, Subscription, subscription_id)
        checkout = await scope.get(self.session, BillingCheckoutSession, checkout_id)
        if (
            subscription is None
            or checkout is None
            or checkout.subscription_id != subscription.id
            or checkout.plan_id != plan.id
        ):
            raise StripeWebhookDataError("Stripe subscription metadata has no matching Checkout.")
        return subscription

    async def sync(
        self,
        snapshot: dict[str, Any],
        *,
        event_id: str | None,
        event_created_at: datetime | None,
    ) -> Subscription | None:
        stripe_subscription_id = _required_string(snapshot, "id")
        stripe_customer_id = _optional_string(snapshot.get("customer"))
        if stripe_customer_id is None:
            raise StripeWebhookDataError("Stripe subscription has no Customer.")
        metadata = _metadata(snapshot)
        existing_result = await self.session.execute(
            select(Subscription)
            .where(Subscription.stripe_subscription_id == stripe_subscription_id)
            .with_for_update()
        )
        existing = existing_result.scalar_one_or_none()
        if existing is None and not APP_SUBSCRIPTION_METADATA_KEYS.intersection(metadata):
            return None
        price_id, period_started_at, period_ends_at = _subscription_item_details(snapshot)
        configured_plan_code = configured_plan_code_for_price(self.settings, price_id)
        if existing is not None:
            self._validate_existing_metadata(existing, metadata)
        if configured_plan_code is None:
            if existing is None:
                raise StripeWebhookDataError("Stripe subscription uses an unknown Price.")
            return await self._fail_closed(
                subscription=existing,
                stripe_subscription_id=stripe_subscription_id,
                stripe_customer_id=stripe_customer_id,
                stripe_price_id=price_id,
                snapshot=snapshot,
                event_id=event_id,
                event_created_at=event_created_at,
            )
        plan, plan_code = await self._plan_for_price(price_id)
        subscription = await self._find_local_subscription(
            existing=existing,
            metadata=metadata,
            plan=plan,
            plan_code=plan_code,
        )
        if subscription is None:
            return None
        if (
            subscription.stripe_customer_id is not None
            and subscription.stripe_customer_id != stripe_customer_id
        ):
            raise StripeWebhookDataError("Stripe Customer does not match the local subscription.")
        if (
            subscription.stripe_subscription_id is not None
            and subscription.stripe_subscription_id != stripe_subscription_id
        ):
            raise StripeWebhookDataError("Stripe subscription ID does not match.")

        try:
            status = SubscriptionStatus(_required_string(snapshot, "status"))
        except ValueError as exc:
            if existing is not None:
                return await self._fail_closed(
                    subscription=existing,
                    stripe_subscription_id=stripe_subscription_id,
                    stripe_customer_id=stripe_customer_id,
                    stripe_price_id=price_id,
                    snapshot=snapshot,
                    event_id=event_id,
                    event_created_at=event_created_at,
                )
            raise StripeWebhookDataError(
                "Stripe returned an unsupported subscription status."
            ) from exc

        subscription.plan_id = plan.id
        subscription.status = status
        subscription.stripe_customer_id = stripe_customer_id
        subscription.stripe_subscription_id = stripe_subscription_id
        subscription.stripe_price_id = price_id
        remote_trial_start = _stripe_datetime(snapshot.get("trial_start"))
        if remote_trial_start is not None and (
            subscription.trial_started_at is None
            or remote_trial_start < subscription.trial_started_at
        ):
            subscription.trial_started_at = remote_trial_start
        subscription.trial_ends_at = _stripe_datetime(snapshot.get("trial_end"))
        subscription.current_period_started_at = period_started_at
        subscription.current_period_ends_at = period_ends_at
        subscription.cancel_at_period_end = bool(snapshot.get("cancel_at_period_end", False))
        subscription.canceled_at = _stripe_datetime(snapshot.get("canceled_at"))
        subscription.ended_at = _stripe_datetime(snapshot.get("ended_at"))
        subscription.last_stripe_synced_at = utc_now()
        if (
            event_id is not None
            and event_created_at is not None
            and (
                subscription.last_stripe_event_created_at is None
                or event_created_at >= subscription.last_stripe_event_created_at
            )
        ):
            subscription.last_stripe_event_id = event_id
            subscription.last_stripe_event_created_at = event_created_at

        checkout_value = metadata.get(STRIPE_METADATA_CHECKOUT_ID)
        if checkout_value is not None:
            checkout_id = _metadata_uuid(metadata, STRIPE_METADATA_CHECKOUT_ID)
            checkout = await TenantScope(subscription.business_id).get(
                self.session,
                BillingCheckoutSession,
                checkout_id,
            )
            if checkout is None or checkout.subscription_id != subscription.id:
                raise StripeWebhookDataError("Stripe Checkout metadata does not match.")
            checkout.status = BillingCheckoutStatus.COMPLETE
            checkout.completed_at = checkout.completed_at or utc_now()
            checkout.stripe_customer_id = stripe_customer_id
            checkout.stripe_subscription_id = stripe_subscription_id

        if status != SubscriptionStatus.TRIALING:
            await self.session.execute(
                delete(SubscriptionEntitlement).where(
                    SubscriptionEntitlement.subscription_id == subscription.id,
                    SubscriptionEntitlement.business_id == subscription.business_id,
                    SubscriptionEntitlement.source == EntitlementSource.TRIAL,
                )
            )
        return subscription


class BillingHistoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(
        self,
        scope: TenantScope,
        *,
        limit: int,
        offset: int,
    ) -> BillingHistoryPage:
        count_result = await self.session.execute(
            select(func.count(BillingInvoice.id)).where(
                BillingInvoice.business_id == scope.business_id
            )
        )
        total = int(count_result.scalar_one())
        invoices_result = await self.session.execute(
            scope.select(BillingInvoice)
            .order_by(BillingInvoice.stripe_created_at.desc(), BillingInvoice.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return BillingHistoryPage(
            items=tuple(invoices_result.scalars().all()),
            total=total,
            limit=limit,
            offset=offset,
        )


class SubscriptionLifecycleService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        gateway: StripeGateway,
    ) -> None:
        self.session = session
        self.settings = settings
        self.gateway = gateway

    async def set_cancel_at_period_end(
        self,
        scope: TenantScope,
        *,
        cancel_at_period_end: bool,
    ) -> Subscription:
        if not self.settings.stripe_enabled:
            raise BillingNotConfiguredError
        result = await self.session.execute(
            scope.select(
                Subscription,
                Subscription.status.in_(CURRENT_SUBSCRIPTION_STATUSES),
            )
            .order_by(Subscription.created_at.desc())
            .with_for_update()
            .limit(1)
        )
        subscription = result.scalar_one_or_none()
        if subscription is None or subscription.stripe_subscription_id is None:
            raise StripeSubscriptionRequiredError
        if subscription.cancel_at_period_end == cancel_at_period_end:
            return subscription

        snapshot = await self.gateway.update_subscription(
            subscription.stripe_subscription_id,
            cancel_at_period_end=cancel_at_period_end,
            idempotency_key=f"spike_subscription_update_{uuid4()}",
        )
        synced = await SubscriptionSynchronizer(
            session=self.session,
            settings=self.settings,
        ).sync(snapshot, event_id=None, event_created_at=None)
        if synced is None or synced.id != subscription.id:
            await self.session.rollback()
            raise StripeWebhookDataError("Stripe returned an unrelated subscription.")
        await self.session.commit()
        return synced


class StripeWebhookService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        gateway: StripeGateway,
    ) -> None:
        self.session = session
        self.settings = settings
        self.gateway = gateway
        self.synchronizer = SubscriptionSynchronizer(
            session=session,
            settings=settings,
        )

    async def _claim_event(
        self,
        *,
        event_id: str,
        event_type: str,
        object_id: str | None,
        event_created_at: datetime,
        livemode: bool,
    ) -> UUID | None:
        event_row_id = uuid4()
        statement = (
            postgresql_insert(StripeWebhookEvent)
            .values(
                id=event_row_id,
                created_at=utc_now(),
                updated_at=utc_now(),
                stripe_event_id=event_id,
                event_type=event_type,
                object_id=object_id,
                stripe_created_at=event_created_at,
                livemode=livemode,
                result=StripeWebhookResult.IGNORED.value,
                processed_at=utc_now(),
            )
            .on_conflict_do_nothing(index_elements=[StripeWebhookEvent.stripe_event_id])
            .returning(StripeWebhookEvent.id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def _complete_checkout(
        self,
        checkout_snapshot: dict[str, Any],
        *,
        expired: bool,
    ) -> StripeWebhookResult:
        metadata = _metadata(checkout_snapshot)
        app_metadata = metadata.get(STRIPE_METADATA_CHECKOUT_ID)
        if app_metadata is None:
            return StripeWebhookResult.IGNORED
        if metadata.get(STRIPE_METADATA_ENVIRONMENT) != self.settings.app_env.value:
            raise StripeWebhookEnvironmentError("Stripe Checkout belongs to another environment.")
        checkout_id = _metadata_uuid(metadata, STRIPE_METADATA_CHECKOUT_ID)
        business_id = _metadata_uuid(metadata, STRIPE_METADATA_BUSINESS_ID)
        subscription_id = _metadata_uuid(metadata, STRIPE_METADATA_SUBSCRIPTION_ID)
        checkout = await TenantScope(business_id).get(
            self.session,
            BillingCheckoutSession,
            checkout_id,
        )
        if (
            checkout is None
            or checkout.subscription_id != subscription_id
            or metadata.get(STRIPE_METADATA_PLAN_CODE) is None
        ):
            raise StripeWebhookDataError("Stripe Checkout metadata does not match.")
        stripe_session_id = _required_string(checkout_snapshot, "id")
        if (
            checkout.stripe_checkout_session_id is not None
            and checkout.stripe_checkout_session_id != stripe_session_id
        ):
            raise StripeWebhookDataError("Stripe Checkout Session ID does not match.")
        checkout.stripe_checkout_session_id = stripe_session_id
        if expired:
            if BillingCheckoutStatus(checkout.status) is not BillingCheckoutStatus.COMPLETE:
                checkout.status = BillingCheckoutStatus.EXPIRED
            return StripeWebhookResult.PROCESSED

        customer_id = _optional_string(checkout_snapshot.get("customer"))
        stripe_subscription_id = _optional_string(checkout_snapshot.get("subscription"))
        if customer_id is None or stripe_subscription_id is None:
            raise StripeWebhookDataError("Completed Stripe Checkout has no subscription.")
        checkout.status = BillingCheckoutStatus.COMPLETE
        checkout.completed_at = checkout.completed_at or utc_now()
        checkout.stripe_customer_id = customer_id
        checkout.stripe_subscription_id = stripe_subscription_id
        return StripeWebhookResult.PROCESSED

    async def _upsert_invoice(
        self,
        invoice: dict[str, Any],
        *,
        subscription: Subscription,
        event_id: str,
        event_created_at: datetime,
    ) -> StripeWebhookResult:
        stripe_invoice_id = _required_string(invoice, "id")
        result = await self.session.execute(
            select(BillingInvoice)
            .where(BillingInvoice.stripe_invoice_id == stripe_invoice_id)
            .with_for_update()
        )
        local = result.scalar_one_or_none()
        if local is not None and event_created_at < local.last_stripe_event_created_at:
            return StripeWebhookResult.IGNORED

        customer_id = _optional_string(invoice.get("customer"))
        if customer_id is None or customer_id != subscription.stripe_customer_id:
            raise StripeWebhookDataError("Stripe Invoice Customer does not match.")
        try:
            status = BillingInvoiceStatus(_required_string(invoice, "status"))
        except ValueError as exc:
            raise StripeWebhookDataError("Stripe returned an unsupported Invoice status.") from exc
        if (
            local is not None
            and event_created_at == local.last_stripe_event_created_at
            and INVOICE_STATUS_RANK[status]
            < INVOICE_STATUS_RANK[BillingInvoiceStatus(local.status)]
        ):
            return StripeWebhookResult.IGNORED
        currency = _required_string(invoice, "currency").upper()
        if len(currency) != 3:
            raise StripeWebhookDataError("Stripe Invoice currency is invalid.")

        status_transitions = invoice.get("status_transitions")
        paid_at = (
            _stripe_datetime(status_transitions.get("paid_at"))
            if isinstance(status_transitions, dict)
            else None
        )
        values = {
            "subscription_id": subscription.id,
            "business_id": subscription.business_id,
            "stripe_customer_id": customer_id,
            "invoice_number": _optional_string(invoice.get("number")),
            "status": status,
            "currency": currency,
            "subtotal_cents": _stripe_integer(invoice, "subtotal"),
            "total_cents": _stripe_integer(invoice, "total"),
            "amount_due_cents": _stripe_integer(invoice, "amount_due"),
            "amount_paid_cents": _stripe_integer(invoice, "amount_paid"),
            "amount_remaining_cents": _stripe_integer(invoice, "amount_remaining"),
            "stripe_created_at": _stripe_datetime(invoice.get("created")),
            "period_started_at": _stripe_datetime(invoice.get("period_start")),
            "period_ends_at": _stripe_datetime(invoice.get("period_end")),
            "paid_at": paid_at,
            "hosted_invoice_url": _optional_string(invoice.get("hosted_invoice_url")),
            "invoice_pdf_url": _optional_string(invoice.get("invoice_pdf")),
            "last_stripe_event_created_at": event_created_at,
            "last_stripe_event_id": event_id,
        }
        if values["stripe_created_at"] is None:
            raise StripeWebhookDataError("Stripe Invoice has no creation time.")
        if local is None:
            now = utc_now()
            insert_result = await self.session.execute(
                postgresql_insert(BillingInvoice)
                .values(
                    id=uuid4(),
                    created_at=now,
                    updated_at=now,
                    stripe_invoice_id=stripe_invoice_id,
                    **values,
                )
                .on_conflict_do_nothing(index_elements=[BillingInvoice.stripe_invoice_id])
                .returning(BillingInvoice.id)
            )
            if insert_result.scalar_one_or_none() is not None:
                return StripeWebhookResult.PROCESSED
            concurrent_result = await self.session.execute(
                select(BillingInvoice)
                .where(BillingInvoice.stripe_invoice_id == stripe_invoice_id)
                .with_for_update()
            )
            local = concurrent_result.scalar_one()
            if event_created_at < local.last_stripe_event_created_at or (
                event_created_at == local.last_stripe_event_created_at
                and INVOICE_STATUS_RANK[status]
                < INVOICE_STATUS_RANK[BillingInvoiceStatus(local.status)]
            ):
                return StripeWebhookResult.IGNORED

        if (
            local.subscription_id != subscription.id
            or local.business_id != subscription.business_id
        ):
            raise StripeWebhookDataError("Stripe Invoice tenant does not match.")
        for key, value in values.items():
            setattr(local, key, value)
        return StripeWebhookResult.PROCESSED

    async def _process_invoice(
        self,
        invoice: dict[str, Any],
        *,
        event_id: str,
        event_created_at: datetime,
    ) -> StripeWebhookResult:
        stripe_subscription_id = _invoice_subscription_id(invoice)
        if stripe_subscription_id is None:
            return StripeWebhookResult.IGNORED
        existing_result = await self.session.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == stripe_subscription_id
            )
        )
        existing = existing_result.scalar_one_or_none()
        try:
            snapshot = await self.gateway.retrieve_subscription(stripe_subscription_id)
        except StripeGatewayError:
            if existing is None:
                raise
            snapshot = {}
        if snapshot:
            subscription = await self.synchronizer.sync(
                snapshot,
                event_id=event_id,
                event_created_at=event_created_at,
            )
        else:
            subscription = existing
        if subscription is None:
            return StripeWebhookResult.IGNORED
        return await self._upsert_invoice(
            invoice,
            subscription=subscription,
            event_id=event_id,
            event_created_at=event_created_at,
        )

    async def process(self, event: StripeObject) -> WebhookProcessResult:
        event_id = _required_string(event, "id")
        event_type = _required_string(event, "type")
        event_created_at = _stripe_datetime(event.get("created"))
        if event_created_at is None:
            raise StripeWebhookDataError("Stripe Event has no creation time.")
        livemode = event.get("livemode")
        if not isinstance(livemode, bool):
            raise StripeWebhookDataError("Stripe Event has no livemode flag.")
        expected_livemode = self.settings.app_env is AppEnvironment.PRODUCTION
        if livemode is not expected_livemode:
            raise StripeWebhookEnvironmentError(
                "Stripe Event livemode does not match the application environment."
            )
        data = event.get("data")
        stripe_object = data.get("object") if isinstance(data, dict) else None
        if not isinstance(stripe_object, dict):
            raise StripeWebhookDataError("Stripe Event has no data object.")
        object_id = _optional_string(stripe_object.get("id"))

        claimed_id = await self._claim_event(
            event_id=event_id,
            event_type=event_type,
            object_id=object_id,
            event_created_at=event_created_at,
            livemode=livemode,
        )
        if claimed_id is None:
            await self.session.rollback()
            return WebhookProcessResult(
                duplicate=True,
                result=StripeWebhookResult.IGNORED,
            )

        try:
            if event_type in SUBSCRIPTION_EVENTS:
                if event_type == "customer.subscription.deleted":
                    snapshot = stripe_object
                else:
                    stripe_subscription_id = _required_string(stripe_object, "id")
                    snapshot = await self.gateway.retrieve_subscription(stripe_subscription_id)
                subscription = await self.synchronizer.sync(
                    snapshot,
                    event_id=event_id,
                    event_created_at=event_created_at,
                )
                result = (
                    StripeWebhookResult.PROCESSED
                    if subscription is not None
                    else StripeWebhookResult.IGNORED
                )
            elif event_type in CHECKOUT_COMPLETE_EVENTS:
                result = await self._complete_checkout(stripe_object, expired=False)
            elif event_type == "checkout.session.expired":
                result = await self._complete_checkout(stripe_object, expired=True)
            elif event_type in INVOICE_EVENTS:
                result = await self._process_invoice(
                    stripe_object,
                    event_id=event_id,
                    event_created_at=event_created_at,
                )
            else:
                result = StripeWebhookResult.IGNORED

            event_row = await self.session.get(StripeWebhookEvent, claimed_id)
            if event_row is None:
                raise StripeWebhookDataError("Claimed Stripe Event disappeared.")
            event_row.result = result
            event_row.processed_at = utc_now()
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return WebhookProcessResult(duplicate=False, result=result)
