from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_tenant_roles
from app.db.session import get_session
from app.models.business import TenantRole
from app.models.user import User
from app.schemas.ai import (
    AIConversationCreate,
    AIConversationPageRead,
    AIConversationRead,
    AIMessageCreate,
    AIMessagePageRead,
    AIMessageRead,
    AIMessageSubmissionRead,
)
from app.services.ai_conversations import (
    AIConversationIntegrityError,
    AIConversationNotFoundError,
    AIConversationService,
    AIConversationTitleError,
    AIMessageContentError,
    AIMessageIdempotencyConflictError,
)
from app.services.tenant import TenantContext

router = APIRouter(prefix="/ai", tags=["AI"])


def _conversation_read(conversation) -> AIConversationRead:
    return AIConversationRead.model_validate(conversation)


def _message_read(message) -> AIMessageRead:
    return AIMessageRead.model_validate(message)


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
)
async def submit_ai_message(
    conversation_id: UUID,
    payload: AIMessageCreate,
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
) -> AIMessageSubmissionRead:
    try:
        submission = await AIConversationService(session).submit_user_message(
            tenant.scope,
            user=current_user,
            conversation_id=conversation_id,
            content=payload.content,
            idempotency_key=idempotency_key,
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
    except AIConversationIntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="AI message could not be persisted.",
        ) from exc

    return AIMessageSubmissionRead(
        conversation=_conversation_read(submission.conversation),
        user_message=_message_read(submission.user_message),
        assistant_message=None,
        replayed=submission.replayed,
    )
