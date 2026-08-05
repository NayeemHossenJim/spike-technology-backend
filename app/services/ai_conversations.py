from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import func, select

from app.models.ai import (
    AI_CONVERSATION_TITLE_MAX_LENGTH,
    AI_MESSAGE_CONTENT_MAX_LENGTH,
    AIConversation,
    AIMessage,
    AIMessageRole,
    AIMessageStatus,
)
from app.models.base import utc_now
from app.models.user import User
from app.services.ai_credits import digest_ai_idempotency_key
from app.services.tenant import TenantScope


class AIConversationError(Exception):
    pass


class AIConversationNotFoundError(AIConversationError):
    pass


class AIConversationIntegrityError(AIConversationError):
    pass


class AIConversationTitleError(AIConversationError, ValueError):
    pass


class AIMessageContentError(AIConversationError, ValueError):
    pass


class AIMessageIdempotencyConflictError(AIConversationError):
    pass


@dataclass(frozen=True, slots=True)
class AIConversationPage:
    items: tuple[AIConversation, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class AIMessagePage:
    items: tuple[AIMessage, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class AIMessageSubmission:
    conversation: AIConversation
    user_message: AIMessage
    replayed: bool


def normalize_ai_conversation_title(title: str | None) -> str | None:
    if title is None:
        return None
    normalized = title.strip()
    if not normalized:
        return None
    if len(normalized) > AI_CONVERSATION_TITLE_MAX_LENGTH:
        raise AIConversationTitleError
    return normalized


def normalize_ai_message_content(content: str) -> str:
    normalized = content.strip()
    if not normalized or len(normalized) > AI_MESSAGE_CONTENT_MAX_LENGTH:
        raise AIMessageContentError
    return normalized


class AIConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        scope: TenantScope,
        *,
        user: User,
        title: str | None = None,
    ) -> AIConversation:
        conversation = AIConversation(
            business_id=scope.business_id,
            created_by_user_id=user.id,
            title=normalize_ai_conversation_title(title),
        )
        self.session.add(conversation)
        try:
            await self.session.commit()
            return conversation
        except IntegrityError as exc:
            await self.session.rollback()
            raise AIConversationIntegrityError from exc

    async def get(
        self,
        scope: TenantScope,
        conversation_id: UUID,
    ) -> AIConversation:
        conversation = await scope.get(self.session, AIConversation, conversation_id)
        if conversation is None:
            raise AIConversationNotFoundError
        return conversation

    async def list(
        self,
        scope: TenantScope,
        *,
        limit: int,
        offset: int,
    ) -> AIConversationPage:
        total = await self.session.scalar(
            select(func.count())
            .select_from(AIConversation)
            .where(AIConversation.business_id == scope.business_id)
        )
        result = await self.session.execute(
            scope.select(AIConversation)
            .order_by(
                AIConversation.updated_at.desc(),
                AIConversation.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return AIConversationPage(
            items=tuple(result.scalars().all()),
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

    async def list_messages(
        self,
        scope: TenantScope,
        *,
        conversation_id: UUID,
        limit: int,
        offset: int,
    ) -> AIMessagePage:
        await self.get(scope, conversation_id)
        total = await self.session.scalar(
            select(func.count())
            .select_from(AIMessage)
            .where(
                AIMessage.business_id == scope.business_id,
                AIMessage.conversation_id == conversation_id,
            )
        )
        result = await self.session.execute(
            scope.select(
                AIMessage,
                AIMessage.conversation_id == conversation_id,
            )
            .order_by(
                AIMessage.created_at.asc(),
                AIMessage.id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return AIMessagePage(
            items=tuple(result.scalars().all()),
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

    async def _message_by_digest(
        self,
        scope: TenantScope,
        digest: str,
    ) -> AIMessage | None:
        result = await self.session.execute(
            scope.select(
                AIMessage,
                AIMessage.idempotency_key_digest == digest,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _validate_replay(
        message: AIMessage,
        *,
        conversation_id: UUID,
        user_id: UUID,
        content: str,
    ) -> None:
        if (
            AIMessageRole(message.role) is not AIMessageRole.USER
            or message.conversation_id != conversation_id
            or message.created_by_user_id != user_id
            or message.content != content
        ):
            raise AIMessageIdempotencyConflictError

    async def _submission_for_existing(
        self,
        scope: TenantScope,
        message: AIMessage,
        *,
        conversation_id: UUID,
        user_id: UUID,
        content: str,
    ) -> AIMessageSubmission:
        self._validate_replay(
            message,
            conversation_id=conversation_id,
            user_id=user_id,
            content=content,
        )
        conversation = await scope.get(
            self.session,
            AIConversation,
            message.conversation_id,
        )
        if conversation is None:
            raise AIConversationIntegrityError
        await self.session.commit()
        return AIMessageSubmission(
            conversation=conversation,
            user_message=message,
            replayed=True,
        )

    async def submit_user_message(
        self,
        scope: TenantScope,
        *,
        user: User,
        conversation_id: UUID,
        content: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> AIMessageSubmission:
        normalized_content = normalize_ai_message_content(content)
        digest = digest_ai_idempotency_key(idempotency_key)
        created_at = now or utc_now()

        try:
            existing = await self._message_by_digest(scope, digest)
            if existing is not None:
                return await self._submission_for_existing(
                    scope,
                    existing,
                    conversation_id=conversation_id,
                    user_id=user.id,
                    content=normalized_content,
                )

            result = await self.session.execute(
                scope.select(
                    AIConversation,
                    AIConversation.id == conversation_id,
                ).with_for_update()
            )
            conversation = result.scalar_one_or_none()
            if conversation is None:
                raise AIConversationNotFoundError

            existing = await self._message_by_digest(scope, digest)
            if existing is not None:
                return await self._submission_for_existing(
                    scope,
                    existing,
                    conversation_id=conversation_id,
                    user_id=user.id,
                    content=normalized_content,
                )

            message = AIMessage(
                business_id=scope.business_id,
                conversation_id=conversation.id,
                role=AIMessageRole.USER,
                status=AIMessageStatus.PENDING,
                content=normalized_content,
                created_by_user_id=user.id,
                idempotency_key_digest=digest,
                created_at=created_at,
                updated_at=created_at,
            )
            conversation.last_message_at = created_at
            conversation.updated_at = created_at
            self.session.add(message)
            await self.session.flush()
            await self.session.commit()
            return AIMessageSubmission(
                conversation=conversation,
                user_message=message,
                replayed=False,
            )
        except IntegrityError as exc:
            await self.session.rollback()
            duplicate = await self._message_by_digest(scope, digest)
            if duplicate is not None:
                try:
                    return await self._submission_for_existing(
                        scope,
                        duplicate,
                        conversation_id=conversation_id,
                        user_id=user.id,
                        content=normalized_content,
                    )
                except AIMessageIdempotencyConflictError:
                    await self.session.rollback()
                    raise
            raise AIConversationIntegrityError from exc
        except Exception:
            await self.session.rollback()
            raise
