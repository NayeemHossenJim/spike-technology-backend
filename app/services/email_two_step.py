from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from asyncer import asyncify
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.config import Settings
from app.core.security import (
    create_six_digit_otp,
    hash_otp,
    utc_now,
    verify_otp,
    verify_otp_against_dummy,
    verify_password,
)
from app.models.auth import (
    EmailTwoStepChallenge,
    EmailTwoStepPurpose,
    RefreshToken,
)
from app.models.user import User
from app.schemas.auth import EmailTwoStepChallengeResponse
from app.schemas.user import UserEmailTwoStepStatusResponse
from app.services.email import EmailSender

logger = logging.getLogger(__name__)


class EmailTwoStepError(Exception):
    pass


class EmailTwoStepNotFoundError(EmailTwoStepError):
    pass


class EmailTwoStepPasswordInvalidError(EmailTwoStepError):
    pass


class EmailTwoStepEmailMismatchError(EmailTwoStepError):
    pass


class EmailTwoStepStateConflictError(EmailTwoStepError):
    pass


class EmailTwoStepChallengeInvalidError(EmailTwoStepError):
    pass


@dataclass(frozen=True, slots=True)
class EmailTwoStepLoginGrant:
    user: User
    remember_me: bool
    challenge: EmailTwoStepChallenge


def mask_email(email: str) -> str:
    """Return a useful delivery hint without disclosing the complete address."""

    local, separator, domain = email.partition("@")
    if not separator:
        return "***"
    if len(local) == 1 or len(local) == 2:
        masked_local = f"{local[0]}*"
    else:
        masked_local = f"{local[0]}{'*' * min(len(local) - 2, 6)}{local[-1]}"
    return f"{masked_local}@{domain}"


class EmailTwoStepService:
    """Own the bounded email-code step shown in Figma's Security Settings flow.

    Email is deliberately described as two-step verification rather than a strong
    MFA factor. Challenge state is still single-use, attempt-bounded, short-lived,
    session-version-bound, and serialized by user-row locks.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        email_sender: EmailSender,
    ) -> None:
        self.session = session
        self.settings = settings
        self.email_sender = email_sender

    async def _lock_user(self, user_id: UUID) -> User:
        user = (
            await self.session.execute(select(User).where(User.id == user_id).with_for_update())
        ).scalar_one_or_none()
        if (
            user is None
            or not user.is_active
            or not user.is_verified
            or user.deleted_at is not None
        ):
            raise EmailTwoStepNotFoundError
        return user

    def status_response(
        self,
        user: User,
        *,
        reauthentication_required: bool = False,
    ) -> UserEmailTwoStepStatusResponse:
        return UserEmailTwoStepStatusResponse(
            enabled=user.email_two_step_enabled,
            enabled_at=user.email_two_step_enabled_at,
            masked_destination=mask_email(user.email),
            reauthentication_required=reauthentication_required,
        )

    async def get_status(self, user_id: UUID) -> UserEmailTwoStepStatusResponse:
        user = await self._lock_user(user_id)
        await self.session.commit()
        return self.status_response(user)

    async def _latest_active_challenge(
        self,
        user_id: UUID,
        purpose: EmailTwoStepPurpose,
    ) -> EmailTwoStepChallenge | None:
        return (
            await self.session.execute(
                select(EmailTwoStepChallenge)
                .where(
                    EmailTwoStepChallenge.user_id == user_id,
                    EmailTwoStepChallenge.purpose == purpose.value,
                    EmailTwoStepChallenge.used_at.is_(None),
                )
                .order_by(EmailTwoStepChallenge.created_at.desc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()

    def _challenge_response(
        self,
        challenge: EmailTwoStepChallenge,
        *,
        email: str,
    ) -> EmailTwoStepChallengeResponse:
        remaining_seconds = max(
            0,
            int((challenge.expires_at - utc_now()).total_seconds()),
        )
        return EmailTwoStepChallengeResponse(
            challenge_id=challenge.id,
            purpose=EmailTwoStepPurpose(challenge.purpose),
            masked_destination=mask_email(email),
            expires_in=remaining_seconds,
        )

    async def _deliver(
        self,
        *,
        user: User,
        raw_otp: str,
        purpose: EmailTwoStepPurpose,
    ) -> None:
        try:
            await self.email_sender.send_email_two_step_otp(
                user.email,
                raw_otp,
                purpose,
            )
        except Exception:
            logger.exception(
                "Email two-step OTP delivery failed",
                extra={"user_id": str(user.id), "purpose": purpose.value},
            )

    async def _notify_setting_change(self, user: User, *, enabled: bool) -> None:
        try:
            await self.email_sender.send_email_two_step_change_notice(
                user.email,
                enabled=enabled,
            )
        except Exception:
            logger.exception(
                "Email two-step setting-change notification failed",
                extra={"user_id": str(user.id), "enabled": enabled},
            )

    async def _issue_locked(
        self,
        user: User,
        *,
        purpose: EmailTwoStepPurpose,
        remember_me: bool = False,
        reuse_during_cooldown: bool = True,
    ) -> EmailTwoStepChallengeResponse:
        now = utc_now()
        current = await self._latest_active_challenge(user.id, purpose)
        if (
            reuse_during_cooldown
            and current is not None
            and current.expires_at > now
            and current.session_version == user.auth_session_version
            and now - current.created_at
            < timedelta(seconds=self.settings.otp_resend_cooldown_seconds)
        ):
            await self.session.commit()
            return self._challenge_response(current, email=user.email)

        await self.session.execute(
            update(EmailTwoStepChallenge)
            .where(
                EmailTwoStepChallenge.user_id == user.id,
                EmailTwoStepChallenge.purpose == purpose.value,
                EmailTwoStepChallenge.used_at.is_(None),
            )
            .values(used_at=now)
        )
        raw_otp = create_six_digit_otp()
        challenge = EmailTwoStepChallenge(
            user_id=user.id,
            purpose=purpose,
            otp_hash=await asyncify(hash_otp)(raw_otp),
            expires_at=now + timedelta(minutes=self.settings.email_two_step_expire_minutes),
            session_version=user.auth_session_version,
            remember_me=remember_me,
        )
        self.session.add(challenge)
        await self.session.commit()
        await self._deliver(user=user, raw_otp=raw_otp, purpose=purpose)
        return self._challenge_response(challenge, email=user.email)

    async def start_login(
        self,
        user: User,
        *,
        remember_me: bool,
    ) -> EmailTwoStepChallengeResponse:
        if not user.email_two_step_enabled:
            raise EmailTwoStepStateConflictError
        return await self._issue_locked(
            user,
            purpose=EmailTwoStepPurpose.LOGIN,
            remember_me=remember_me,
        )

    async def request_enable(
        self,
        user_id: UUID,
        *,
        email: str,
        current_password: str,
    ) -> EmailTwoStepChallengeResponse:
        user = await self._lock_user(user_id)
        if not await asyncify(verify_password)(current_password, user.password_hash):
            raise EmailTwoStepPasswordInvalidError
        if email.strip().lower() != user.email:
            raise EmailTwoStepEmailMismatchError
        if user.email_two_step_enabled:
            raise EmailTwoStepStateConflictError
        return await self._issue_locked(user, purpose=EmailTwoStepPurpose.ENABLE)

    async def request_disable(
        self,
        user_id: UUID,
        *,
        current_password: str,
    ) -> EmailTwoStepChallengeResponse:
        user = await self._lock_user(user_id)
        if not await asyncify(verify_password)(current_password, user.password_hash):
            raise EmailTwoStepPasswordInvalidError
        if not user.email_two_step_enabled:
            raise EmailTwoStepStateConflictError
        return await self._issue_locked(user, purpose=EmailTwoStepPurpose.DISABLE)

    async def _lock_challenge_and_user(
        self,
        challenge_id: UUID,
        *,
        purpose: EmailTwoStepPurpose,
        expected_user_id: UUID | None = None,
    ) -> tuple[EmailTwoStepChallenge, User]:
        probe = await self.session.get(EmailTwoStepChallenge, challenge_id)
        if probe is None or (expected_user_id is not None and probe.user_id != expected_user_id):
            raise EmailTwoStepChallengeInvalidError
        user = await self._lock_user(probe.user_id)
        challenge = (
            await self.session.execute(
                select(EmailTwoStepChallenge)
                .where(
                    EmailTwoStepChallenge.id == challenge_id,
                    EmailTwoStepChallenge.user_id == user.id,
                    EmailTwoStepChallenge.purpose == purpose.value,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if challenge is None:
            raise EmailTwoStepChallengeInvalidError
        return challenge, user

    async def _validate_otp(
        self,
        challenge: EmailTwoStepChallenge,
        user: User,
        raw_otp: str,
    ) -> None:
        now = utc_now()
        invalid_state = (
            challenge.used_at is not None
            or challenge.expires_at <= now
            or challenge.session_version != user.auth_session_version
        )
        if invalid_state:
            if challenge.used_at is None:
                challenge.used_at = now
                await self.session.commit()
            await asyncify(verify_otp_against_dummy)(raw_otp)
            raise EmailTwoStepChallengeInvalidError

        if challenge.attempt_count >= self.settings.otp_max_attempts:
            challenge.used_at = now
            await self.session.commit()
            await asyncify(verify_otp_against_dummy)(raw_otp)
            raise EmailTwoStepChallengeInvalidError

        if await asyncify(verify_otp)(raw_otp, challenge.otp_hash):
            return

        challenge.attempt_count += 1
        if challenge.attempt_count >= self.settings.otp_max_attempts:
            challenge.used_at = now
        await self.session.commit()
        raise EmailTwoStepChallengeInvalidError

    async def _revoke_sessions(self, user_id: UUID, *, now) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

    async def _invalidate_challenges(self, user_id: UUID, *, now) -> None:
        await self.session.execute(
            update(EmailTwoStepChallenge)
            .where(
                EmailTwoStepChallenge.user_id == user_id,
                EmailTwoStepChallenge.used_at.is_(None),
            )
            .values(used_at=now)
        )

    async def confirm_enable(
        self,
        user_id: UUID,
        challenge_id: UUID,
        *,
        raw_otp: str,
    ) -> UserEmailTwoStepStatusResponse:
        try:
            challenge, user = await self._lock_challenge_and_user(
                challenge_id,
                purpose=EmailTwoStepPurpose.ENABLE,
                expected_user_id=user_id,
            )
        except EmailTwoStepChallengeInvalidError:
            await asyncify(verify_otp_against_dummy)(raw_otp)
            raise
        if user.email_two_step_enabled:
            raise EmailTwoStepStateConflictError
        await self._validate_otp(challenge, user, raw_otp)

        now = utc_now()
        challenge.used_at = now
        user.email_two_step_enabled = True
        user.email_two_step_enabled_at = now
        user.auth_session_version += 1
        await self._revoke_sessions(user.id, now=now)
        await self._invalidate_challenges(user.id, now=now)
        await self.session.commit()
        await self._notify_setting_change(user, enabled=True)
        return self.status_response(user, reauthentication_required=True)

    async def confirm_disable(
        self,
        user_id: UUID,
        challenge_id: UUID,
        *,
        raw_otp: str,
    ) -> UserEmailTwoStepStatusResponse:
        try:
            challenge, user = await self._lock_challenge_and_user(
                challenge_id,
                purpose=EmailTwoStepPurpose.DISABLE,
                expected_user_id=user_id,
            )
        except EmailTwoStepChallengeInvalidError:
            await asyncify(verify_otp_against_dummy)(raw_otp)
            raise
        if not user.email_two_step_enabled:
            raise EmailTwoStepStateConflictError
        await self._validate_otp(challenge, user, raw_otp)

        now = utc_now()
        challenge.used_at = now
        user.email_two_step_enabled = False
        user.email_two_step_enabled_at = None
        user.auth_session_version += 1
        await self._revoke_sessions(user.id, now=now)
        await self._invalidate_challenges(user.id, now=now)
        await self.session.commit()
        await self._notify_setting_change(user, enabled=False)
        return self.status_response(user, reauthentication_required=True)

    async def consume_login(
        self,
        challenge_id: UUID,
        *,
        raw_otp: str,
    ) -> EmailTwoStepLoginGrant:
        try:
            challenge, user = await self._lock_challenge_and_user(
                challenge_id,
                purpose=EmailTwoStepPurpose.LOGIN,
            )
        except EmailTwoStepChallengeInvalidError:
            await asyncify(verify_otp_against_dummy)(raw_otp)
            raise
        if not user.email_two_step_enabled:
            challenge.used_at = utc_now()
            await self.session.commit()
            await asyncify(verify_otp_against_dummy)(raw_otp)
            raise EmailTwoStepChallengeInvalidError
        await self._validate_otp(challenge, user, raw_otp)
        challenge.used_at = utc_now()
        return EmailTwoStepLoginGrant(
            user=user,
            remember_me=challenge.remember_me,
            challenge=challenge,
        )

    async def resend(
        self,
        challenge_id: UUID,
        *,
        allowed_purposes: tuple[EmailTwoStepPurpose, ...],
        expected_user_id: UUID | None = None,
    ) -> EmailTwoStepChallengeResponse:
        probe = await self.session.get(EmailTwoStepChallenge, challenge_id)
        if probe is None or (expected_user_id is not None and probe.user_id != expected_user_id):
            raise EmailTwoStepChallengeInvalidError
        try:
            purpose = EmailTwoStepPurpose(probe.purpose)
        except ValueError as exc:
            raise EmailTwoStepChallengeInvalidError from exc
        if purpose not in allowed_purposes:
            raise EmailTwoStepChallengeInvalidError

        challenge, user = await self._lock_challenge_and_user(
            challenge_id,
            purpose=purpose,
            expected_user_id=expected_user_id,
        )
        now = utc_now()
        if (
            challenge.used_at is not None
            or challenge.expires_at <= now
            or challenge.session_version != user.auth_session_version
            or (purpose is EmailTwoStepPurpose.LOGIN and not user.email_two_step_enabled)
            or (purpose is EmailTwoStepPurpose.ENABLE and user.email_two_step_enabled)
            or (purpose is EmailTwoStepPurpose.DISABLE and not user.email_two_step_enabled)
        ):
            if challenge.used_at is None:
                challenge.used_at = now
                await self.session.commit()
            raise EmailTwoStepChallengeInvalidError

        if now - challenge.created_at < timedelta(
            seconds=self.settings.otp_resend_cooldown_seconds
        ):
            await self.session.commit()
            return self._challenge_response(challenge, email=user.email)

        return await self._issue_locked(
            user,
            purpose=purpose,
            remember_me=challenge.remember_me,
            reuse_during_cooldown=False,
        )
