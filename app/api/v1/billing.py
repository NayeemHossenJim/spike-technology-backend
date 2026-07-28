from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_tenant_roles
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.models.billing import BillingCheckoutStatus
from app.models.business import TenantRole
from app.models.user import User
from app.schemas.billing import (
    BillingHistoryRead,
    BillingInvoiceRead,
    CheckoutSessionCreate,
    CheckoutSessionRead,
    StripeWebhookRead,
    SubscriptionLifecycleRead,
)
from app.services.billing import (
    BillingCheckoutConflictError,
    BillingCheckoutInProgressError,
    BillingHistoryService,
    BillingNotConfiguredError,
    BillingPlanNotCheckoutEligibleError,
    BillingPlanNotFoundError,
    BillingPriceConfigurationError,
    CheckoutService,
    StripeSubscriptionRequiredError,
    StripeWebhookDataError,
    StripeWebhookEnvironmentError,
    StripeWebhookService,
    SubscriptionLifecycleService,
)
from app.services.stripe_gateway import (
    StripeGateway,
    StripeGatewayError,
    StripeSdkGateway,
    StripeWebhookSignatureError,
)
from app.services.tenant import TenantContext

MAX_STRIPE_WEBHOOK_BYTES = 1_000_000

router = APIRouter(prefix="/billing", tags=["Billing"])


def get_stripe_gateway(
    settings: Annotated[Settings, Depends(get_settings)],
) -> StripeGateway:
    return StripeSdkGateway(settings)


def _checkout_read(
    *,
    checkout,
    plan_code,
) -> CheckoutSessionRead:
    return CheckoutSessionRead(
        id=checkout.id,
        subscription_id=checkout.subscription_id,
        plan_code=plan_code,
        status=BillingCheckoutStatus(checkout.status),
        checkout_url=checkout.checkout_url,
        expires_at=checkout.expires_at,
    )


@router.post(
    "/checkout-sessions",
    response_model=CheckoutSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_checkout_session(
    payload: CheckoutSessionCreate,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=255,
            pattern=r"^[!-~]+$",
        ),
    ],
    current_user: Annotated[User, Depends(get_current_user)],
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    gateway: Annotated[StripeGateway, Depends(get_stripe_gateway)],
) -> CheckoutSessionRead:
    try:
        created = await CheckoutService(
            session=session,
            settings=settings,
            gateway=gateway,
        ).create(
            scope=tenant.scope,
            user=current_user,
            plan_code=payload.plan_code,
            idempotency_key=idempotency_key,
        )
    except BillingNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not configured.",
        ) from exc
    except BillingPlanNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found.",
        ) from exc
    except BillingPlanNotCheckoutEligibleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="This Plan requires a sales-assisted subscription.",
        ) from exc
    except BillingPriceConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The selected billing Plan is temporarily unavailable.",
        ) from exc
    except BillingCheckoutInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A Checkout Session is already in progress for this business.",
        ) from exc
    except BillingCheckoutConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The requested Checkout operation conflicts with the current subscription.",
        ) from exc
    except StripeGatewayError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe is temporarily unavailable.",
        ) from exc
    return _checkout_read(
        checkout=created.checkout,
        plan_code=created.plan_code,
    )


@router.post(
    "/subscription/cancel",
    response_model=SubscriptionLifecycleRead,
)
async def schedule_subscription_cancellation(
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    gateway: Annotated[StripeGateway, Depends(get_stripe_gateway)],
) -> SubscriptionLifecycleRead:
    return await _set_cancellation(
        tenant=tenant,
        session=session,
        settings=settings,
        gateway=gateway,
        cancel_at_period_end=True,
    )


@router.post(
    "/subscription/resume",
    response_model=SubscriptionLifecycleRead,
)
async def resume_scheduled_subscription(
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    gateway: Annotated[StripeGateway, Depends(get_stripe_gateway)],
) -> SubscriptionLifecycleRead:
    return await _set_cancellation(
        tenant=tenant,
        session=session,
        settings=settings,
        gateway=gateway,
        cancel_at_period_end=False,
    )


async def _set_cancellation(
    *,
    tenant: TenantContext,
    session: AsyncSession,
    settings: Settings,
    gateway: StripeGateway,
    cancel_at_period_end: bool,
) -> SubscriptionLifecycleRead:
    try:
        subscription = await SubscriptionLifecycleService(
            session=session,
            settings=settings,
            gateway=gateway,
        ).set_cancel_at_period_end(
            tenant.scope,
            cancel_at_period_end=cancel_at_period_end,
        )
    except BillingNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not configured.",
        ) from exc
    except StripeSubscriptionRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No Stripe-managed subscription is available.",
        ) from exc
    except (StripeGatewayError, StripeWebhookDataError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe could not update the subscription.",
        ) from exc
    return SubscriptionLifecycleRead.model_validate(subscription, from_attributes=True)


@router.get("/history", response_model=BillingHistoryRead)
async def read_billing_history(
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> BillingHistoryRead:
    page = await BillingHistoryService(session).list(
        tenant.scope,
        limit=limit,
        offset=offset,
    )
    return BillingHistoryRead(
        items=[BillingInvoiceRead.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/webhooks/stripe", response_model=StripeWebhookRead)
async def receive_stripe_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    gateway: Annotated[StripeGateway, Depends(get_stripe_gateway)],
    stripe_signature: Annotated[
        str | None,
        Header(alias="Stripe-Signature"),
    ] = None,
) -> StripeWebhookRead:
    if not settings.stripe_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not configured.",
        )
    if stripe_signature is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe signature.",
        )
    payload = await request.body()
    if len(payload) > MAX_STRIPE_WEBHOOK_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Stripe webhook payload is too large.",
        )
    try:
        event = gateway.construct_webhook_event(
            payload=payload,
            signature=stripe_signature,
        )
    except StripeWebhookSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe webhook signature.",
        ) from exc

    try:
        processed = await StripeWebhookService(
            session=session,
            settings=settings,
            gateway=gateway,
        ).process(event)
    except (StripeWebhookDataError, StripeWebhookEnvironmentError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe webhook data.",
        ) from exc
    except StripeGatewayError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe webhook processing is temporarily unavailable.",
        ) from exc
    return StripeWebhookRead(
        duplicate=processed.duplicate,
        result=processed.result,
    )
