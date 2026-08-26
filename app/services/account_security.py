from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from asyncer import asyncify
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.config import Settings
from app.core.security import (
    create_six_digit_otp,
    hash_otp,
    hash_password,
    utc_now,
    verify_otp,
    verify_otp_against_dummy,
    verify_password,
)
from app.models.auth import (
    EmailChangeOTP,
    EmailVerificationOTP,
    PasswordResetOTP,
    RefreshToken,
)
from app.models.billing import BillingCheckoutSession, BillingCheckoutStatus
from app.models.business import Business, RoleAssignment
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User, UserRole
from app.schemas.user import (
    UserAccountDeleteRequest,
    UserEmailChangeConfirm,
    UserEmailChangeRequest,
    UserPasswordChangeRequest,
)
from app.services.auth import normalize_email
from app.services.email import EmailSender
from app.services.subscriptions import CURRENT_SUBSCRIPTION_STATUSES

logger = logging.getLogger(__name__)


class AccountSecurityError(Exception):
    pass


class AccountSecurityNotFoundError(AccountSecurityError):
    pass


class CurrentPasswordInvalidError(AccountSecurityError):
    pass


class CurrentEmailMismatchError(AccountSecurityError):
    pass


class EmailChangeConflictError(AccountSecurityError):
    pass


class EmailChangeOTPInvalidError(AccountSecurityError):
    pass


class PasswordReuseError(AccountSecurityError):
    pass


class AccountDeletionBillingConflictError(AccountSecurityError):
    pass


class LastSuperAdminDeletionError(AccountSecurityError):
    pass


@dataclass(frozen=True, slots=True)
class SecurityMutationResult:
    sessions_revoked: int


class AccountSecurityService:
    """Own verified self-service credential changes and safe account closure."""

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
        result = await self.session.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        user = result.scalar_one_or_none()
        if user is None or not user.is_active or user.deleted_at is not None:
            raise AccountSecurityNotFoundError
        return user

    @staticmethod
    async def _verify_current_password(user: User, password: str) -> None:
        if not await asyncify(verify_password)(password, user.password_hash):
            raise CurrentPasswordInvalidError

    async def _revoke_sessions(self, user_id: UUID, *, now) -> int:
        result = await self.session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        return int(result.rowcount or 0)

    async def _invalidate_auth_otps(self, user_id: UUID, *, now) -> None:
        for otp_model in (EmailVerificationOTP, PasswordResetOTP, EmailChangeOTP):
            await self.session.execute(
                update(otp_model)
                .where(
                    otp_model.user_id == user_id,
                    otp_model.used_at.is_(None),
                )
                .values(used_at=now)
            )

    async def request_email_change(
        self,
        user_id: UUID,
        *,
        payload: UserEmailChangeRequest,
    ) -> bool:
        """Create one pending new-address challenge; return whether a new OTP was sent."""

        try:
            user = await self._lock_user(user_id)
            await self._verify_current_password(user, payload.current_password)

            old_email = normalize_email(str(payload.old_email))
            new_email = normalize_email(str(payload.new_email))
            if old_email != user.email:
                raise CurrentEmailMismatchError
            if new_email == user.email:
                raise EmailChangeConflictError

            existing_user_id = await self.session.scalar(
                select(User.id).where(User.email == new_email)
            )
            if existing_user_id is not None:
                raise EmailChangeConflictError

            latest_sent_at = await self.session.scalar(
                select(EmailChangeOTP.created_at)
                .where(EmailChangeOTP.user_id == user.id)
                .order_by(EmailChangeOTP.created_at.desc())
                .limit(1)
            )
            if latest_sent_at is not None and utc_now() - latest_sent_at < timedelta(
                seconds=self.settings.otp_resend_cooldown_seconds
            ):
                await self.session.commit()
                return False

            now = utc_now()
            await self.session.execute(
                update(EmailChangeOTP)
                .where(
                    EmailChangeOTP.user_id == user.id,
                    EmailChangeOTP.used_at.is_(None),
                )
                .values(used_at=now)
            )
            raw_otp = create_six_digit_otp()
            self.session.add(
                EmailChangeOTP(
                    user_id=user.id,
                    new_email=new_email,
                    otp_hash=await asyncify(hash_otp)(raw_otp),
                    expires_at=now
                    + timedelta(minutes=self.settings.email_verification_expire_minutes),
                )
            )
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise EmailChangeConflictError from exc
        except Exception:
            await self.session.rollback()
            raise

        try:
            await self.email_sender.send_email_change_otp(new_email, raw_otp)
        except Exception:
            logger.exception(
                "Email-change OTP delivery failed",
                extra={"user_id": str(user_id)},
            )
        return True

    async def _validate_email_change_otp(
        self,
        record: EmailChangeOTP | None,
        raw_otp: str,
    ) -> EmailChangeOTP:
        if record is None:
            await asyncify(verify_otp_against_dummy)(raw_otp)
            raise EmailChangeOTPInvalidError

        now = utc_now()
        if record.used_at is not None or record.expires_at <= now:
            if record.used_at is None:
                record.used_at = now
                await self.session.commit()
            await asyncify(verify_otp_against_dummy)(raw_otp)
            raise EmailChangeOTPInvalidError

        if record.attempt_count >= self.settings.otp_max_attempts:
            record.used_at = now
            await self.session.commit()
            await asyncify(verify_otp_against_dummy)(raw_otp)
            raise EmailChangeOTPInvalidError

        if await asyncify(verify_otp)(raw_otp, record.otp_hash):
            return record

        record.attempt_count += 1
        if record.attempt_count >= self.settings.otp_max_attempts:
            record.used_at = now
        await self.session.commit()
        raise EmailChangeOTPInvalidError

    async def confirm_email_change(
        self,
        user_id: UUID,
        *,
        payload: UserEmailChangeConfirm,
    ) -> SecurityMutationResult:
        try:
            user = await self._lock_user(user_id)
            new_email = normalize_email(str(payload.new_email))
            result = await self.session.execute(
                select(EmailChangeOTP)
                .where(
                    EmailChangeOTP.user_id == user.id,
                    EmailChangeOTP.new_email == new_email,
                    EmailChangeOTP.used_at.is_(None),
                )
                .order_by(EmailChangeOTP.created_at.desc())
                .limit(1)
                .with_for_update()
            )
            otp_record = await self._validate_email_change_otp(
                result.scalar_one_or_none(),
                payload.otp,
            )

            existing_user_id = await self.session.scalar(
                select(User.id).where(User.email == new_email, User.id != user.id)
            )
            if existing_user_id is not None:
                raise EmailChangeConflictError

            now = utc_now()
            user.email = new_email
            user.is_verified = True
            user.auth_session_version += 1
            otp_record.used_at = now
            sessions_revoked = await self._revoke_sessions(user.id, now=now)
            await self._invalidate_auth_otps(user.id, now=now)
            await self.session.commit()
            return SecurityMutationResult(sessions_revoked=sessions_revoked)
        except IntegrityError as exc:
            await self.session.rollback()
            raise EmailChangeConflictError from exc
        except Exception:
            await self.session.rollback()
            raise

    async def change_password(
        self,
        user_id: UUID,
        *,
        payload: UserPasswordChangeRequest,
    ) -> SecurityMutationResult:
        try:
            user = await self._lock_user(user_id)
            await self._verify_current_password(user, payload.current_password)
            if await asyncify(verify_password)(payload.new_password, user.password_hash):
                raise PasswordReuseError

            now = utc_now()
            user.password_hash = await asyncify(hash_password)(payload.new_password)
            user.auth_session_version += 1
            sessions_revoked = await self._revoke_sessions(user.id, now=now)
            await self._invalidate_auth_otps(user.id, now=now)
            await self.session.commit()
            return SecurityMutationResult(sessions_revoked=sessions_revoked)
        except Exception:
            await self.session.rollback()
            raise

    async def _guard_last_super_admin(self, user: User) -> None:
        if user.role != UserRole.SUPER_ADMIN:
            return
        result = await self.session.execute(
            select(User.id)
            .where(
                User.role == UserRole.SUPER_ADMIN,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .order_by(User.id)
            .with_for_update()
        )
        if len(result.scalars().all()) <= 1:
            raise LastSuperAdminDeletionError

    async def delete_account(
        self,
        user_id: UUID,
        *,
        payload: UserAccountDeleteRequest,
    ) -> SecurityMutationResult:
        try:
            user = await self._lock_user(user_id)
            await self._verify_current_password(user, payload.current_password)
            await self._guard_last_super_admin(user)

            business = (
                await self.session.execute(
                    select(Business).where(Business.owner_user_id == user.id).with_for_update()
                )
            ).scalar_one_or_none()

            role_assignment = None
            current_subscription = None
            if business is not None:
                current_subscription = (
                    await self.session.execute(
                        select(Subscription)
                        .where(
                            Subscription.business_id == business.id,
                            Subscription.status.in_(CURRENT_SUBSCRIPTION_STATUSES),
                        )
                        .order_by(Subscription.created_at.desc())
                        .limit(1)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                # Checkout creation locks the current subscription before publishing
                # its local operation. Lock it first, then inspect Checkout rows, so
                # deletion cannot miss a concurrently created paid-flow attempt.
                open_checkout_id = await self.session.scalar(
                    select(BillingCheckoutSession.id)
                    .where(
                        BillingCheckoutSession.business_id == business.id,
                        BillingCheckoutSession.status.in_(
                            (
                                BillingCheckoutStatus.PENDING,
                                BillingCheckoutStatus.OPEN,
                            )
                        ),
                    )
                    .limit(1)
                    .with_for_update()
                )
                completed_checkout_id = None
                if current_subscription is not None:
                    completed_checkout_id = await self.session.scalar(
                        select(BillingCheckoutSession.id)
                        .where(
                            BillingCheckoutSession.business_id == business.id,
                            BillingCheckoutSession.subscription_id == current_subscription.id,
                            BillingCheckoutSession.status == BillingCheckoutStatus.COMPLETE,
                            BillingCheckoutSession.stripe_subscription_id.is_not(None),
                        )
                        .limit(1)
                        .with_for_update()
                    )
                if (
                    open_checkout_id is not None
                    or completed_checkout_id is not None
                    or (
                        current_subscription is not None
                        and current_subscription.stripe_subscription_id is not None
                    )
                ):
                    raise AccountDeletionBillingConflictError

                role_assignment = (
                    await self.session.execute(
                        select(RoleAssignment)
                        .where(RoleAssignment.user_id == user.id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()

            now = utc_now()
            if current_subscription is not None:
                current_subscription.status = SubscriptionStatus.CANCELED
                current_subscription.cancel_at_period_end = False
                current_subscription.canceled_at = now
                current_subscription.ended_at = now
            if role_assignment is not None:
                role_assignment.is_active = False

            anonymization_nonce = secrets.token_hex(16)
            user.email = f"deleted-{user.id.hex}-{anonymization_nonce}@deleted.invalid"
            user.full_name = "Deleted User"
            user.username = None
            user.phone_number = None
            user.street_address = None
            user.city = None
            user.state = None
            user.postal_code = None
            user.bio = None
            user.industry = None
            user.job_role = None
            user.password_hash = await asyncify(hash_password)(secrets.token_urlsafe(48))
            user.is_active = False
            user.is_verified = False
            user.last_login_at = None
            user.deleted_at = now
            user.auth_session_version += 1

            sessions_revoked = await self._revoke_sessions(user.id, now=now)
            await self._invalidate_auth_otps(user.id, now=now)
            await self.session.commit()
            return SecurityMutationResult(sessions_revoked=sessions_revoked)
        except Exception:
            await self.session.rollback()
            raise
