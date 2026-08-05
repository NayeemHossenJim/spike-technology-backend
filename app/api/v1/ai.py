from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    enforce_ai_rate_limit_dependency,
    get_current_user,
    require_tenant_roles,
)
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.models.business import TenantRole
from app.models.user import User
from app.schemas.ai import (
    AIConversationCreate,
    AIConversationPageRead,
    AIConversationRead,
    AICreditLedgerEntryRead,
    AICreditUsageSummaryRead,
    AIMessageCreate,
    AIMessagePageRead,
    AIMessageRead,
    AIMessageSubmissionRead,
    AIUsageHistoryRead,
)
from app.services.ai_conversations import (
    AIConversationIntegrityError,
    AIConversationNotFoundError,
    AIConversationService,
    AIConversationTitleError,
    AIMessageContentError,
    AIMessageIdempotencyConflictError,
)
from app.services.ai_credits import AICreditService, AICreditUsageSummary
from app.services.ai_execution import (
    AIExecutionErrorCode,
    AIExecutionFailedError,
    AIExecutionIntegrityError,
    AIExecutionService,
)
from app.services.gemini_gateway import GeminiGateway, get_gemini_gateway
from app.services.subscriptions import (
    EntitlementDeniedError,
    SubscriptionInactiveError,
    SubscriptionRequiredError,
)
from app.services.tenant import TenantContext

router = APIRouter(prefix="/ai", tags=["AI"])

AI_FAILURE_RESPONSES: dict[AIExecutionErrorCode, tuple[int, str]] = {
    AIExecutionErrorCode.SUBSCRIPTION_REQUIRED: (
        status.HTTP_402_PAYMENT_REQUIRED,
        "An active subscription is required to use AI.",
    ),
    AIExecutionErrorCode.SUBSCRIPTION_INACTIVE: (
        status.HTTP_402_PAYMENT_REQUIRED,
        "The current subscription cannot use AI.",
    ),
    AIExecutionErrorCode.ENTITLEMENT_DENIED: (
        status.HTTP_403_FORBIDDEN,
        "AI access is not enabled for the current subscription.",
    ),
    AIExecutionErrorCode.CREDIT_LIMIT_REACHED: (
        status.HTTP_429_TOO_MANY_REQUESTS,
        "The AI response limit has been reached for the current period.",
    ),
    AIExecutionErrorCode.PROVIDER_REJECTED: (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "The AI provider rejected this request.",
    ),
    AIExecutionErrorCode.PROVIDER_BLOCKED: (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "The AI provider blocked this request.",
    ),
    AIExecutionErrorCode.PROVIDER_INCOMPLETE: (
        status.HTTP_502_BAD_GATEWAY,
        "The AI provider returned an incomplete response.",
    ),
    AIExecutionErrorCode.PROVIDER_INVALID: (
        status.HTTP_502_BAD_GATEWAY,
        "The AI provider returned an invalid response.",
    ),
    AIExecutionErrorCode.RESPONSE_TOO_LARGE: (
        status.HTTP_502_BAD_GATEWAY,
        "The AI provider returned a response that was too large.",
    ),
    AIExecutionErrorCode.PROVIDER_CONFIGURATION: (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "AI service is temporarily unavailable.",
    ),
    AIExecutionErrorCode.PROVIDER_TEMPORARY: (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "AI service is temporarily unavailable.",
    ),
    AIExecutionErrorCode.PERSISTENCE_FAILURE: (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "AI service could not persist the response.",
    ),
    AIExecutionErrorCode.EXECUTION_INTEGRITY: (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "AI service is temporarily unavailable.",
    ),
}


def _conversation_read(conversation) -> AIConversationRead:
    return AIConversationRead.model_validate(conversation)


def _message_read(message) -> AIMessageRead:
    return AIMessageRead.model_validate(message)


def _usage_read(usage: AICreditUsageSummary | None) -> AICreditUsageSummaryRead | None:
    if usage is None:
        return None
    return AICreditUsageSummaryRead(
        account_id=usage.account_id,
        subscription_id=usage.subscription_id,
        business_id=usage.business_id,
        limit_value=usage.limit_value,
        reserved_count=usage.reserved_count,
        consumed_count=usage.consumed_count,
        remaining=usage.remaining,
        period_started_at=usage.period_started_at,
        period_ends_at=usage.period_ends_at,
    )


def _execution_http_error(exc: AIExecutionFailedError) -> HTTPException:
    status_code, detail = AI_FAILURE_RESPONSES[exc.error_code]
    return HTTPException(status_code=status_code, detail=detail)


@router.post(
    "/conversations",
    response_model=AIConversationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_ai_conversation(
    payload: AIConversationCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AIConversationRead:
    try:
        conversation = await AIConversationService(session).create(
            tenant.scope,
            user=current_user,
            title=payload.title,
        )
    except AIConversationTitleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="AI conversation title is invalid.",
        ) from exc
    except AIConversationIntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="AI conversation could not be created.",
        ) from exc
    return _conversation_read(conversation)


@router.get(
    "/conversations",
    response_model=AIConversationPageRead,
)
async def list_ai_conversations(
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> AIConversationPageRead:
    page = await AIConversationService(session).list(
        tenant.scope,
        limit=limit,
        offset=offset,
    )
    return AIConversationPageRead(
        items=[_conversation_read(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/usage",
    response_model=AIUsageHistoryRead,
)
async def read_ai_usage(
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> AIUsageHistoryRead:
    try:
        history = await AICreditService(session).usage_history(
            tenant.scope,
            limit=limit,
            offset=offset,
        )
    except SubscriptionRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No current subscription was found.",
        ) from exc
    except SubscriptionInactiveError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="The current subscription cannot use AI.",
        ) from exc
    except EntitlementDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AI access is not enabled for the current subscription.",
        ) from exc

    current = _usage_read(history.current)
    assert current is not None
    return AIUsageHistoryRead(
        current=current,
        items=[
            AICreditLedgerEntryRead(
                id=item.id,
                account_id=item.account_id,
                subscription_id=item.subscription_id,
                status=item.status,
                quantity=item.quantity,
                reserved_at=item.reserved_at,
                consumed_at=item.consumed_at,
                released_at=item.released_at,
                release_reason=item.release_reason,
            )
            for item in history.items
        ],
        total=history.total,
        limit=history.limit,
        offset=history.offset,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=AIConversationRead,
)
async def read_ai_conversation(
    conversation_id: UUID,
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AIConversationRead:
    try:
        conversation = await AIConversationService(session).get(
            tenant.scope,
            conversation_id,
        )
    except AIConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI conversation not found.",
        ) from exc
    return _conversation_read(conversation)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=AIMessagePageRead,
)
async def list_ai_messages(
    conversation_id: UUID,
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> AIMessagePageRead:
    try:
        page = await AIConversationService(session).list_messages(
            tenant.scope,
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
        )
    except AIConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI conversation not found.",
        ) from exc
    return AIMessagePageRead(
        items=[_message_read(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=AIMessageSubmissionRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "A completed idempotent replay."},
        202: {"description": "The idempotent request is already processing."},
        402: {"description": "An active subscription is required."},
        403: {"description": "AI entitlement is disabled."},
        409: {"description": "The idempotency key conflicts."},
        429: {"description": "AI rate or credit limit reached."},
        502: {"description": "The provider returned an unusable response."},
        503: {"description": "AI service is temporarily unavailable."},
    },
)
async def submit_ai_message(
    conversation_id: UUID,
    payload: AIMessageCreate,
    request: Request,
    response: Response,
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
    gateway: Annotated[GeminiGateway, Depends(get_gemini_gateway)],
    settings: Annotated[Settings, Depends(get_settings)],
    _: Annotated[None, Depends(enforce_ai_rate_limit_dependency)],
) -> AIMessageSubmissionRead:
    try:
        result = await AIExecutionService(
            session,
            gateway=gateway,
            settings=settings,
        ).execute(
            tenant.scope,
            user=current_user,
            conversation_id=conversation_id,
            content=payload.content,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
        )
    except AIConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI conversation not found.",
        ) from exc
    except AIMessageIdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key was already used for a different AI message.",
        ) from exc
    except AIMessageContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="AI message content is invalid.",
        ) from exc
    except AIExecutionFailedError as exc:
        raise _execution_http_error(exc) from exc
    except (AIConversationIntegrityError, AIExecutionIntegrityError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI service is temporarily unavailable.",
        ) from exc

    if result.in_progress:
        response.status_code = status.HTTP_202_ACCEPTED
    elif result.replayed:
        response.status_code = status.HTTP_200_OK

    return AIMessageSubmissionRead(
        conversation=_conversation_read(result.conversation),
        user_message=_message_read(result.user_message),
        assistant_message=(
            _message_read(result.assistant_message)
            if result.assistant_message is not None
            else None
        ),
        usage=_usage_read(result.usage),
        replayed=result.replayed,
    )
