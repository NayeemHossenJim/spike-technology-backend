from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from time import perf_counter
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.ai import (
    AI_MESSAGE_CONTENT_MAX_LENGTH,
    AIConversation,
    AICreditAccount,
    AICreditLedgerEntry,
    AICreditLedgerStatus,
    AIMessage,
    AIMessageRole,
    AIMessageStatus,
)
from app.models.base import utc_now
from app.models.user import User
from app.services.ai_conversations import (
    AIConversationService,
    AIMessageSubmission,
)
from app.services.ai_credits import (
    AICreditIntegrityError,
    AICreditLedgerSnapshot,
    AICreditService,
    AICreditUsageSummary,
)
from app.services.gemini_gateway import (
    GeminiBlockedError,
    GeminiConfigurationError,
    GeminiGateway,
    GeminiGatewayError,
    GeminiGeneration,
    GeminiIncompleteResponseError,
    GeminiInvalidResponseError,
    GeminiRequestRejectedError,
    GeminiRequestValidationError,
    GeminiTemporaryError,
)
from app.services.subscriptions import (
    EntitlementDeniedError,
    EntitlementLimitReachedError,
    SubscriptionInactiveError,
    SubscriptionRequiredError,
)
from app.services.tenant import TenantScope

logger = logging.getLogger(__name__)

AI_PROMPT_HISTORY_MESSAGE_LIMIT = 12
AI_PROMPT_MAX_CHARACTERS = 30_000
AI_SYSTEM_INSTRUCTION = (
    "You are Spike Technology's financial analysis assistant. "
    "Answer clearly and accurately from the information supplied. "
    "State when information is missing and never invent financial facts."
)


class AIExecutionErrorCode(StrEnum):
    SUBSCRIPTION_REQUIRED = "subscription_required"
    SUBSCRIPTION_INACTIVE = "subscription_inactive"
    ENTITLEMENT_DENIED = "entitlement_denied"
    CREDIT_LIMIT_REACHED = "credit_limit_reached"
    PROVIDER_CONFIGURATION = "provider_configuration"
    PROVIDER_TEMPORARY = "provider_temporary"
    PROVIDER_REJECTED = "provider_rejected"
    PROVIDER_BLOCKED = "provider_blocked"
    PROVIDER_INCOMPLETE = "provider_incomplete"
    PROVIDER_INVALID = "provider_invalid"
    RESPONSE_TOO_LARGE = "response_too_large"
    PERSISTENCE_FAILURE = "persistence_failure"
    EXECUTION_INTEGRITY = "execution_integrity"


class AIExecutionError(Exception):
    pass


class AIExecutionFailedError(AIExecutionError):
    def __init__(self, error_code: AIExecutionErrorCode) -> None:
        self.error_code = error_code
        super().__init__(error_code.value)


class AIExecutionIntegrityError(AIExecutionError):
    pass


@dataclass(frozen=True, slots=True)
class AIExecutionResult:
    conversation: AIConversation
    user_message: AIMessage
    assistant_message: AIMessage | None
    usage: AICreditUsageSummary | None
    replayed: bool
    in_progress: bool


@dataclass(frozen=True, slots=True)
class _ExecutionClaim:
    conversation: AIConversation
    user_message: AIMessage
    assistant_message: AIMessage | None
    prompt: str | None
    token: UUID | None
    usage: AICreditUsageSummary | None
    in_progress: bool
    completed: bool


def _usage_from_snapshot(snapshot: AICreditLedgerSnapshot) -> AICreditUsageSummary:
    return AICreditUsageSummary(
        account_id=snapshot.account_id,
        subscription_id=snapshot.subscription_id,
        business_id=snapshot.business_id,
        limit_value=snapshot.limit_value,
        reserved_count=snapshot.reserved_count,
        consumed_count=snapshot.consumed_count,
        remaining=snapshot.remaining,
        period_started_at=snapshot.period_started_at,
        period_ends_at=snapshot.period_ends_at,
    )


def _provider_error_code(exc: GeminiGatewayError) -> AIExecutionErrorCode:
    if isinstance(exc, GeminiConfigurationError):
        return AIExecutionErrorCode.PROVIDER_CONFIGURATION
    if isinstance(exc, GeminiTemporaryError):
        return AIExecutionErrorCode.PROVIDER_TEMPORARY
    if isinstance(exc, GeminiBlockedError):
        return AIExecutionErrorCode.PROVIDER_BLOCKED
    if isinstance(exc, GeminiRequestRejectedError):
        return AIExecutionErrorCode.PROVIDER_REJECTED
    if isinstance(exc, GeminiIncompleteResponseError):
        return AIExecutionErrorCode.PROVIDER_INCOMPLETE
    if isinstance(exc, (GeminiInvalidResponseError, GeminiRequestValidationError)):
        return AIExecutionErrorCode.PROVIDER_INVALID
    return AIExecutionErrorCode.PROVIDER_TEMPORARY


def _reservation_error_code(exc: Exception) -> AIExecutionErrorCode:
    if isinstance(exc, SubscriptionRequiredError):
        return AIExecutionErrorCode.SUBSCRIPTION_REQUIRED
    if isinstance(exc, SubscriptionInactiveError):
        return AIExecutionErrorCode.SUBSCRIPTION_INACTIVE
    if isinstance(exc, EntitlementDeniedError):
        return AIExecutionErrorCode.ENTITLEMENT_DENIED
    if isinstance(exc, EntitlementLimitReachedError):
        return AIExecutionErrorCode.CREDIT_LIMIT_REACHED
    return AIExecutionErrorCode.EXECUTION_INTEGRITY


def _bounded_prompt(messages: list[AIMessage]) -> str:
    segments = [
        f"{'User' if AIMessageRole(item.role) is AIMessageRole.USER else 'Assistant'}: "
        f"{item.content.strip()}"
        for item in messages
        if item.content.strip()
    ]
    selected: list[str] = []
    remaining = AI_PROMPT_MAX_CHARACTERS
    for segment in reversed(segments):
        if remaining <= 0:
            break
        if len(segment) > remaining:
            segment = segment[-remaining:]
        selected.append(segment)
        remaining -= len(segment) + 2
    selected.reverse()
    return "\n\n".join(selected)


class AIExecutionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        gateway: GeminiGateway,
        settings: Settings,
    ) -> None:
        self.session = session
        self.gateway = gateway
        self.settings = settings
        self.conversations = AIConversationService(session)
        self.credits = AICreditService(session)

    @property
    def lease_duration(self) -> timedelta:
        seconds = max(self.settings.gemini_request_timeout_seconds + 30, 90)
        return timedelta(seconds=seconds)

    async def _assistant_for_user(
        self,
        scope: TenantScope,
        user_message: AIMessage,
    ) -> AIMessage | None:
        result = await self.session.execute(
            scope.select(
                AIMessage,
                AIMessage.conversation_id == user_message.conversation_id,
                AIMessage.reply_to_message_id == user_message.id,
                AIMessage.role == AIMessageRole.ASSISTANT,
            )
        )
        return result.scalar_one_or_none()

    async def _existing_result(
        self,
        scope: TenantScope,
        submission: AIMessageSubmission,
    ) -> AIExecutionResult | None:
        status = AIMessageStatus(submission.user_message.status)
        if status is AIMessageStatus.PENDING:
            return None
        if status is AIMessageStatus.FAILED:
            error_code = submission.user_message.error_code
            if error_code is None:
                raise AIExecutionIntegrityError
            try:
                normalized = AIExecutionErrorCode(error_code)
            except ValueError as exc:
                raise AIExecutionIntegrityError from exc
            raise AIExecutionFailedError(normalized)

        assistant = await self._assistant_for_user(scope, submission.user_message)
        if assistant is None or submission.user_message.credit_ledger_entry_id is None:
            raise AIExecutionIntegrityError
        snapshot = await self.credits.snapshot(
            scope,
            entry_id=submission.user_message.credit_ledger_entry_id,
        )
        await self.session.commit()
        return AIExecutionResult(
            conversation=submission.conversation,
            user_message=submission.user_message,
            assistant_message=assistant,
            usage=_usage_from_snapshot(snapshot),
            replayed=True,
            in_progress=False,
        )

    async def _history_for_claim(
        self,
        scope: TenantScope,
        *,
        conversation_id: UUID,
        current_message_id: UUID,
    ) -> list[AIMessage]:
        result = await self.session.execute(
            scope.select(
                AIMessage,
                AIMessage.conversation_id == conversation_id,
                (
                    (AIMessage.status == AIMessageStatus.COMPLETED)
                    | (AIMessage.id == current_message_id)
                ),
            )
            .order_by(AIMessage.created_at.desc(), AIMessage.id.desc())
            .limit(AI_PROMPT_HISTORY_MESSAGE_LIMIT)
        )
        return list(reversed(result.scalars().all()))

    async def _claim(
        self,
        scope: TenantScope,
        *,
        message_id: UUID,
        reservation: AICreditLedgerSnapshot,
        now: datetime,
    ) -> _ExecutionClaim:
        try:
            result = await self.session.execute(
                scope.select(
                    AIMessage,
                    AIMessage.id == message_id,
                ).with_for_update()
            )
            message = result.scalar_one_or_none()
            if message is None:
                raise AIExecutionIntegrityError

            conversation_result = await self.session.execute(
                scope.select(
                    AIConversation,
                    AIConversation.id == message.conversation_id,
                ).with_for_update()
            )
            conversation = conversation_result.scalar_one_or_none()
            if conversation is None:
                raise AIExecutionIntegrityError

            status = AIMessageStatus(message.status)
            if status is AIMessageStatus.COMPLETED:
                assistant = await self._assistant_for_user(scope, message)
                if assistant is None:
                    raise AIExecutionIntegrityError
                snapshot = await self.credits.snapshot(
                    scope,
                    entry_id=reservation.entry_id,
                )
                await self.session.commit()
                return _ExecutionClaim(
                    conversation=conversation,
                    user_message=message,
                    assistant_message=assistant,
                    prompt=None,
                    token=None,
                    usage=_usage_from_snapshot(snapshot),
                    in_progress=False,
                    completed=True,
                )
            if status is AIMessageStatus.FAILED:
                error_code = message.error_code
                await self.session.commit()
                if error_code is None:
                    raise AIExecutionIntegrityError
                try:
                    normalized = AIExecutionErrorCode(error_code)
                except ValueError as exc:
                    raise AIExecutionIntegrityError from exc
                raise AIExecutionFailedError(normalized)

            if reservation.status is not AICreditLedgerStatus.RESERVED:
                raise AIExecutionIntegrityError
            if message.credit_ledger_entry_id not in {None, reservation.entry_id}:
                raise AIExecutionIntegrityError

            lease_active = (
                message.processing_token is not None
                and message.processing_lease_expires_at is not None
                and message.processing_lease_expires_at > now
            )
            if lease_active:
                await self.session.commit()
                return _ExecutionClaim(
                    conversation=conversation,
                    user_message=message,
                    assistant_message=None,
                    prompt=None,
                    token=None,
                    usage=_usage_from_snapshot(reservation),
                    in_progress=True,
                    completed=False,
                )

            token = uuid4()
            message.credit_ledger_entry_id = reservation.entry_id
            message.processing_token = token
            message.processing_lease_expires_at = now + self.lease_duration
            message.updated_at = now
            history = await self._history_for_claim(
                scope,
                conversation_id=message.conversation_id,
                current_message_id=message.id,
            )
            prompt = _bounded_prompt(history)
            if not prompt:
                raise AIExecutionIntegrityError
            await self.session.commit()
            return _ExecutionClaim(
                conversation=conversation,
                user_message=message,
                assistant_message=None,
                prompt=prompt,
                token=token,
                usage=_usage_from_snapshot(reservation),
                in_progress=False,
                completed=False,
            )
        except Exception:
            await self.session.rollback()
            raise

    async def _lock_ledger(
        self,
        scope: TenantScope,
        *,
        entry_id: UUID,
    ) -> tuple[AICreditLedgerEntry, AICreditAccount]:
        entry_result = await self.session.execute(
            scope.select(
                AICreditLedgerEntry,
                AICreditLedgerEntry.id == entry_id,
            ).with_for_update()
        )
        entry = entry_result.scalar_one_or_none()
        if entry is None:
            raise AIExecutionIntegrityError

        account_result = await self.session.execute(
            scope.select(
                AICreditAccount,
                AICreditAccount.id == entry.account_id,
                AICreditAccount.subscription_id == entry.subscription_id,
            ).with_for_update()
        )
        account = account_result.scalar_one_or_none()
        if account is None:
            raise AIExecutionIntegrityError
        return entry, account

    async def _mark_pre_reservation_failure(
        self,
        scope: TenantScope,
        *,
        message_id: UUID,
        error_code: AIExecutionErrorCode,
        now: datetime,
    ) -> None:
        try:
            result = await self.session.execute(
                scope.select(
                    AIMessage,
                    AIMessage.id == message_id,
                ).with_for_update()
            )
            message = result.scalar_one_or_none()
            if message is None:
                raise AIExecutionIntegrityError
            if AIMessageStatus(message.status) is not AIMessageStatus.PENDING:
                await self.session.commit()
                return
            if message.credit_ledger_entry_id is not None:
                await self.session.commit()
                return
            message.status = AIMessageStatus.FAILED
            message.completed_at = now
            message.error_code = error_code.value
            message.processing_token = None
            message.processing_lease_expires_at = None
            message.updated_at = now
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

    async def _fail_claim(
        self,
        scope: TenantScope,
        *,
        message_id: UUID,
        entry_id: UUID,
        token: UUID,
        error_code: AIExecutionErrorCode,
        now: datetime,
    ) -> bool:
        try:
            result = await self.session.execute(
                scope.select(
                    AIMessage,
                    AIMessage.id == message_id,
                ).with_for_update()
            )
            message = result.scalar_one_or_none()
            if message is None:
                raise AIExecutionIntegrityError

            status = AIMessageStatus(message.status)
            if status is not AIMessageStatus.PENDING:
                await self.session.commit()
                return False
            if message.processing_token != token:
                await self.session.commit()
                return False
            if message.credit_ledger_entry_id != entry_id:
                raise AIExecutionIntegrityError

            entry, account = await self._lock_ledger(scope, entry_id=entry_id)
            ledger_status = AICreditLedgerStatus(entry.status)
            if ledger_status is AICreditLedgerStatus.RESERVED:
                if account.reserved_count < 1:
                    raise AIExecutionIntegrityError
                account.reserved_count -= 1
                entry.status = AICreditLedgerStatus.RELEASED
                entry.released_at = now
                entry.release_reason = error_code.value
                entry.consumed_at = None
            elif ledger_status is not AICreditLedgerStatus.RELEASED:
                raise AIExecutionIntegrityError

            message.status = AIMessageStatus.FAILED
            message.completed_at = now
            message.error_code = error_code.value
            message.processing_token = None
            message.processing_lease_expires_at = None
            message.updated_at = now
            await self.session.commit()
            return True
        except Exception:
            await self.session.rollback()
            raise

    async def _complete_claim(
        self,
        scope: TenantScope,
        *,
        message_id: UUID,
        entry_id: UUID,
        token: UUID,
        generation: GeminiGeneration,
        now: datetime,
    ) -> AIExecutionResult:
        try:
            result = await self.session.execute(
                scope.select(
                    AIMessage,
                    AIMessage.id == message_id,
                ).with_for_update()
            )
            message = result.scalar_one_or_none()
            if message is None:
                raise AIExecutionIntegrityError

            conversation_result = await self.session.execute(
                scope.select(
                    AIConversation,
                    AIConversation.id == message.conversation_id,
                ).with_for_update()
            )
            conversation = conversation_result.scalar_one_or_none()
            if conversation is None:
                raise AIExecutionIntegrityError

            if AIMessageStatus(message.status) is AIMessageStatus.COMPLETED:
                assistant = await self._assistant_for_user(scope, message)
                if assistant is None:
                    raise AIExecutionIntegrityError
                snapshot = await self.credits.snapshot(scope, entry_id=entry_id)
                await self.session.commit()
                return AIExecutionResult(
                    conversation=conversation,
                    user_message=message,
                    assistant_message=assistant,
                    usage=_usage_from_snapshot(snapshot),
                    replayed=True,
                    in_progress=False,
                )
            if AIMessageStatus(message.status) is AIMessageStatus.FAILED:
                error_code = message.error_code
                await self.session.commit()
                if error_code is None:
                    raise AIExecutionIntegrityError
                try:
                    normalized = AIExecutionErrorCode(error_code)
                except ValueError as exc:
                    raise AIExecutionIntegrityError from exc
                raise AIExecutionFailedError(normalized)
            if (
                AIMessageStatus(message.status) is not AIMessageStatus.PENDING
                or message.processing_token != token
                or message.credit_ledger_entry_id != entry_id
            ):
                await self.session.commit()
                return AIExecutionResult(
                    conversation=conversation,
                    user_message=message,
                    assistant_message=None,
                    usage=None,
                    replayed=True,
                    in_progress=True,
                )

            entry, account = await self._lock_ledger(scope, entry_id=entry_id)
            if AICreditLedgerStatus(entry.status) is not AICreditLedgerStatus.RESERVED:
                raise AIExecutionIntegrityError
            if account.reserved_count < 1:
                raise AIExecutionIntegrityError

            assistant = AIMessage(
                business_id=scope.business_id,
                conversation_id=message.conversation_id,
                role=AIMessageRole.ASSISTANT,
                status=AIMessageStatus.COMPLETED,
                content=generation.text,
                created_by_user_id=None,
                reply_to_message_id=message.id,
                idempotency_key_digest=None,
                credit_ledger_entry_id=entry.id,
                processing_token=None,
                processing_lease_expires_at=None,
                provider_response_id=generation.provider_response_id,
                provider_model=generation.model_version,
                provider_finish_reason=generation.finish_reason,
                prompt_token_count=generation.prompt_token_count,
                response_token_count=generation.response_token_count,
                thoughts_token_count=generation.thoughts_token_count,
                total_token_count=generation.total_token_count,
                completed_at=now,
                error_code=None,
                created_at=now,
                updated_at=now,
            )
            self.session.add(assistant)

            message.status = AIMessageStatus.COMPLETED
            message.completed_at = now
            message.error_code = None
            message.processing_token = None
            message.processing_lease_expires_at = None
            message.updated_at = now

            account.reserved_count -= 1
            account.consumed_count += 1
            entry.status = AICreditLedgerStatus.CONSUMED
            entry.consumed_at = now
            entry.released_at = None
            entry.release_reason = None

            conversation.last_message_at = now
            conversation.updated_at = now

            await self.session.flush()
            snapshot = AICreditService._snapshot(entry, account, replayed=False)
            await self.session.commit()
            return AIExecutionResult(
                conversation=conversation,
                user_message=message,
                assistant_message=assistant,
                usage=_usage_from_snapshot(snapshot),
                replayed=False,
                in_progress=False,
            )
        except IntegrityError as exc:
            await self.session.rollback()
            raise AIExecutionIntegrityError from exc
        except Exception:
            await self.session.rollback()
            raise

    async def execute(
        self,
        scope: TenantScope,
        *,
        user: User,
        conversation_id: UUID,
        content: str,
        idempotency_key: str,
        request_id: str,
        now: datetime | None = None,
    ) -> AIExecutionResult:
        started = perf_counter()
        checked_at = now or utc_now()
        submission = await self.conversations.submit_user_message(
            scope,
            user=user,
            conversation_id=conversation_id,
            content=content,
            idempotency_key=idempotency_key,
            now=checked_at,
        )
        # A later credit-service rollback expires ORM instances in this shared
        # session. Keep immutable identifiers before reservation starts.
        user_id = user.id
        user_message_id = submission.user_message.id

        if submission.replayed:
            existing = await self._existing_result(scope, submission)
            if existing is not None:
                logger.info(
                    "ai_request_replayed",
                    extra={
                        "request_id": request_id,
                        "business_id": str(scope.business_id),
                        "user_id": str(user_id),
                        "conversation_id": str(conversation_id),
                        "message_id": str(user_message_id),
                        "outcome": "completed",
                        "replayed": True,
                    },
                )
                return existing

        try:
            reservation = await self.credits.reserve(
                scope,
                idempotency_key=idempotency_key,
                now=checked_at,
            )
        except (
            SubscriptionRequiredError,
            SubscriptionInactiveError,
            EntitlementDeniedError,
            EntitlementLimitReachedError,
            AICreditIntegrityError,
        ) as exc:
            error_code = _reservation_error_code(exc)
            await self._mark_pre_reservation_failure(
                scope,
                message_id=user_message_id,
                error_code=error_code,
                now=checked_at,
            )
            logger.warning(
                "ai_request_failed",
                extra={
                    "request_id": request_id,
                    "business_id": str(scope.business_id),
                    "user_id": str(user_id),
                    "conversation_id": str(conversation_id),
                    "message_id": str(user_message_id),
                    "outcome": "failed",
                    "error_code": error_code.value,
                    "duration_ms": int((perf_counter() - started) * 1000),
                },
            )
            raise AIExecutionFailedError(error_code) from exc

        claim = await self._claim(
            scope,
            message_id=user_message_id,
            reservation=reservation,
            now=checked_at,
        )
        if claim.completed:
            return AIExecutionResult(
                conversation=claim.conversation,
                user_message=claim.user_message,
                assistant_message=claim.assistant_message,
                usage=claim.usage,
                replayed=True,
                in_progress=False,
            )
        if claim.in_progress or claim.token is None or claim.prompt is None:
            logger.info(
                "ai_request_in_progress",
                extra={
                    "request_id": request_id,
                    "business_id": str(scope.business_id),
                    "user_id": str(user_id),
                    "conversation_id": str(conversation_id),
                    "message_id": str(user_message_id),
                    "ledger_entry_id": str(reservation.entry_id),
                    "outcome": "in_progress",
                    "replayed": submission.replayed,
                },
            )
            return AIExecutionResult(
                conversation=claim.conversation,
                user_message=claim.user_message,
                assistant_message=None,
                usage=claim.usage,
                replayed=submission.replayed,
                in_progress=True,
            )

        try:
            generation = await self.gateway.generate(
                prompt=claim.prompt,
                system_instruction=AI_SYSTEM_INSTRUCTION,
            )
            if len(generation.text) > AI_MESSAGE_CONTENT_MAX_LENGTH:
                error_code = AIExecutionErrorCode.RESPONSE_TOO_LARGE
                await self._fail_claim(
                    scope,
                    message_id=user_message_id,
                    entry_id=reservation.entry_id,
                    token=claim.token,
                    error_code=error_code,
                    now=utc_now(),
                )
                raise AIExecutionFailedError(error_code)
        except GeminiGatewayError as exc:
            error_code = _provider_error_code(exc)
            await self._fail_claim(
                scope,
                message_id=user_message_id,
                entry_id=reservation.entry_id,
                token=claim.token,
                error_code=error_code,
                now=utc_now(),
            )
            logger.warning(
                "ai_request_failed",
                extra={
                    "request_id": request_id,
                    "business_id": str(scope.business_id),
                    "user_id": str(user_id),
                    "conversation_id": str(conversation_id),
                    "message_id": str(user_message_id),
                    "ledger_entry_id": str(reservation.entry_id),
                    "outcome": "failed",
                    "error_code": error_code.value,
                    "duration_ms": int((perf_counter() - started) * 1000),
                },
            )
            raise AIExecutionFailedError(error_code) from exc

        try:
            completed = await self._complete_claim(
                scope,
                message_id=user_message_id,
                entry_id=reservation.entry_id,
                token=claim.token,
                generation=generation,
                now=utc_now(),
            )
        except AIExecutionIntegrityError as exc:
            try:
                await self._fail_claim(
                    scope,
                    message_id=user_message_id,
                    entry_id=reservation.entry_id,
                    token=claim.token,
                    error_code=AIExecutionErrorCode.PERSISTENCE_FAILURE,
                    now=utc_now(),
                )
            except Exception:
                logger.exception(
                    "ai_request_recovery_failed",
                    extra={
                        "request_id": request_id,
                        "business_id": str(scope.business_id),
                        "message_id": str(user_message_id),
                        "ledger_entry_id": str(reservation.entry_id),
                    },
                )
            raise AIExecutionFailedError(AIExecutionErrorCode.PERSISTENCE_FAILURE) from exc

        logger.info(
            "ai_request_completed",
            extra={
                "request_id": request_id,
                "business_id": str(scope.business_id),
                "user_id": str(user_id),
                "conversation_id": str(conversation_id),
                "message_id": str(user_message_id),
                "assistant_message_id": (
                    str(completed.assistant_message.id)
                    if completed.assistant_message is not None
                    else None
                ),
                "ledger_entry_id": str(reservation.entry_id),
                "provider_response_id": generation.provider_response_id,
                "provider_model": generation.model_version,
                "prompt_token_count": generation.prompt_token_count,
                "response_token_count": generation.response_token_count,
                "total_token_count": generation.total_token_count,
                "outcome": "completed",
                "duration_ms": int((perf_counter() - started) * 1000),
            },
        )
        return AIExecutionResult(
            conversation=completed.conversation,
            user_message=completed.user_message,
            assistant_message=completed.assistant_message,
            usage=completed.usage,
            replayed=submission.replayed or completed.replayed,
            in_progress=completed.in_progress,
        )
