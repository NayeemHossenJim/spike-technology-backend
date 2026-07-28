from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

import stripe

from app.core.config import Settings

StripeObject = dict[str, Any]


class StripeGatewayError(Exception):
    """A Stripe API request could not be completed safely."""


class StripeCheckoutRejectedError(StripeGatewayError):
    """Stripe definitively rejected Checkout before creating a Session."""


class StripeWebhookSignatureError(Exception):
    """The webhook payload was malformed or did not have a valid signature."""


class StripeGateway(Protocol):
    async def retrieve_price(
        self,
        stripe_price_id: str,
    ) -> StripeObject: ...

    async def create_checkout_session(
        self,
        *,
        params: StripeObject,
        idempotency_key: str,
    ) -> StripeObject: ...

    async def update_subscription(
        self,
        stripe_subscription_id: str,
        *,
        cancel_at_period_end: bool,
        idempotency_key: str,
    ) -> StripeObject: ...

    async def retrieve_subscription(
        self,
        stripe_subscription_id: str,
    ) -> StripeObject: ...

    def construct_webhook_event(
        self,
        *,
        payload: bytes,
        signature: str,
    ) -> StripeObject: ...


def _as_plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _as_plain_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_plain_value(item) for item in value]
    return value


def _as_plain_dict(value: Any) -> StripeObject:
    converted = _as_plain_value(value)
    if not isinstance(converted, dict):
        raise StripeGatewayError("Stripe returned an unexpected response body.")
    return converted


class StripeSdkGateway:
    """Small adapter around stripe-python so application services remain testable."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: stripe.StripeClient | None = None

    @property
    def client(self) -> stripe.StripeClient:
        if self._client is None:
            if self.settings.stripe_secret_key is None:
                raise StripeGatewayError("Stripe billing is not configured.")
            self._client = stripe.StripeClient(
                self.settings.stripe_secret_key.get_secret_value(),
                max_network_retries=2,
            )
        return self._client

    async def create_checkout_session(
        self,
        *,
        params: StripeObject,
        idempotency_key: str,
    ) -> StripeObject:
        try:
            session = await self.client.v1.checkout.sessions.create_async(
                params,
                {"idempotency_key": idempotency_key},
            )
        except (
            stripe.APIConnectionError,
            stripe.APIError,
            stripe.IdempotencyError,
            stripe.RateLimitError,
        ) as exc:
            raise StripeGatewayError("Stripe could not create the Checkout Session.") from exc
        except (
            stripe.AuthenticationError,
            stripe.CardError,
            stripe.InvalidRequestError,
            stripe.PermissionError,
        ) as exc:
            raise StripeCheckoutRejectedError(
                "Stripe rejected the Checkout Session request."
            ) from exc
        except stripe.StripeError as exc:
            raise StripeGatewayError("Stripe could not create the Checkout Session.") from exc
        return _as_plain_dict(session)

    async def retrieve_price(
        self,
        stripe_price_id: str,
    ) -> StripeObject:
        try:
            price = await self.client.v1.prices.retrieve_async(stripe_price_id)
        except stripe.StripeError as exc:
            raise StripeGatewayError("Stripe could not retrieve the configured Price.") from exc
        return _as_plain_dict(price)

    async def update_subscription(
        self,
        stripe_subscription_id: str,
        *,
        cancel_at_period_end: bool,
        idempotency_key: str,
    ) -> StripeObject:
        try:
            subscription = await self.client.v1.subscriptions.update_async(
                stripe_subscription_id,
                {"cancel_at_period_end": cancel_at_period_end},
                {"idempotency_key": idempotency_key},
            )
        except stripe.StripeError as exc:
            raise StripeGatewayError("Stripe could not update the subscription.") from exc
        return _as_plain_dict(subscription)

    async def retrieve_subscription(
        self,
        stripe_subscription_id: str,
    ) -> StripeObject:
        try:
            subscription = await self.client.v1.subscriptions.retrieve_async(
                stripe_subscription_id,
                {"expand": ["items.data.price"]},
            )
        except stripe.StripeError as exc:
            raise StripeGatewayError("Stripe could not retrieve the subscription.") from exc
        return _as_plain_dict(subscription)

    def construct_webhook_event(
        self,
        *,
        payload: bytes,
        signature: str,
    ) -> StripeObject:
        if self.settings.stripe_webhook_secret is None:
            raise StripeWebhookSignatureError("Stripe webhook verification is not configured.")
        try:
            event = stripe.Webhook.construct_event(
                payload,
                signature,
                self.settings.stripe_webhook_secret.get_secret_value(),
                tolerance=self.settings.stripe_webhook_tolerance_seconds,
            )
        except (ValueError, stripe.SignatureVerificationError) as exc:
            raise StripeWebhookSignatureError("The Stripe signature is invalid.") from exc
        return _as_plain_dict(event)
