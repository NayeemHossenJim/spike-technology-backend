from __future__ import annotations

import logging
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
    TokenType,
    create_jwt_token,
    create_six_digit_otp,
    decode_jwt_token,
    hash_opaque_token,
    hash_otp,
    hash_password,
    utc_now,
    verify_otp,
    verify_otp_against_dummy,
    verify_password,
    verify_password_against_dummy,
)
from app.models.auth import EmailVerificationOTP, PasswordResetOTP, RefreshToken
from app.models.user import User
from app.schemas.auth import (
    AccessTokenResponse,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    VerifyEmailOTPRequest,
    VerifyPasswordResetOTPRequest,
)
from app.services.email import EmailSender

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    pass


class EmailVerificationRequiredError(Exception):
    pass


class InactiveAccountError(Exception):
    pass


class TokenValidationError(Exception):
    pass


class DuplicateEmailError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SessionTokens:
    access: AccessTokenResponse
    refresh_token: str
    remember_me: bool


def normalize_email(email: str) -> str:
    return email.strip().lower()


class AuthService:
    def __init__(
        self, session: AsyncSession, settings: Settings, email_sender: EmailSender
    ) -> None:
        self.session = session
        self.settings = settings
        self.email_sender = email_sender

    async def _get_user_by_email(
        self,
        email: str,
        *,
        lock_for_otp: bool = False,
    ) -> User | None:
        statement = select(User).where(User.email == normalize_email(email))
        if lock_for_otp:
            # PostgreSQL FOR NO KEY UPDATE serializes per-user OTP state while remaining
            # compatible with foreign-key checks made by concurrent session inserts.
            statement = statement.with_for_update(key_share=True)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def _get_user_by_id(self, user_id: UUID) -> User | None:
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def _resend_is_allowed(
        self,
        otp_model: type[EmailVerificationOTP] | type[PasswordResetOTP],
        user_id: UUID,
    ) -> bool:
        result = await self.session.execute(
            select(otp_model.created_at)
            .where(otp_model.user_id == user_id)
            .order_by(otp_model.created_at.desc())
            .limit(1)
        )
        last_sent_at = result.scalar_one_or_none()
        if last_sent_at is None:
            return True
        return utc_now() - last_sent_at >= timedelta(
            seconds=self.settings.otp_resend_cooldown_seconds
        )

    async def _issue_otp(
        self,
        *,
        user: User,
        otp_model: type[EmailVerificationOTP] | type[PasswordResetOTP],
        expires_in_minutes: int,
        enforce_resend_cooldown: bool,
    ) -> str | None:
        # Serialize OTP issuance per user. This makes the cooldown check, invalidation,
        # and replacement insert one atomic operation even under concurrent requests.
        await self.session.execute(
            select(User.id).where(User.id == user.id).with_for_update(key_share=True)
        )
        if enforce_resend_cooldown and not await self._resend_is_allowed(otp_model, user.id):
            return None

        now = utc_now()
        await self.session.execute(
            update(otp_model)
            .where(otp_model.user_id == user.id, otp_model.used_at.is_(None))
            .values(used_at=now)
        )
        raw_otp = create_six_digit_otp()
        self.session.add(
            otp_model(
                user_id=user.id,
                otp_hash=await asyncify(hash_otp)(raw_otp),
                expires_at=now + timedelta(minutes=expires_in_minutes),
            )
        )
        return raw_otp

    async def _get_active_otp(
        self,
        otp_model: type[EmailVerificationOTP] | type[PasswordResetOTP],
        user_id: UUID,
    ) -> EmailVerificationOTP | PasswordResetOTP | None:
        result = await self.session.execute(
            select(otp_model)
            .where(otp_model.user_id == user_id, otp_model.used_at.is_(None))
            .order_by(otp_model.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def _validate_otp(
        self,
        otp_record: EmailVerificationOTP | PasswordResetOTP | None,
        raw_otp: str,
    ) -> EmailVerificationOTP | PasswordResetOTP:
        if otp_record is None:
            await asyncify(verify_otp_against_dummy)(raw_otp)
            raise TokenValidationError

        now = utc_now()
        if otp_record.used_at or otp_record.expires_at <= now:
            if otp_record.used_at is None:
                otp_record.used_at = now
                await self.session.commit()
            await asyncify(verify_otp_against_dummy)(raw_otp)
            raise TokenValidationError

        if otp_record.attempt_count >= self.settings.otp_max_attempts:
            otp_record.used_at = now
            await self.session.commit()
            await asyncify(verify_otp_against_dummy)(raw_otp)
            raise TokenValidationError

        if await asyncify(verify_otp)(raw_otp, otp_record.otp_hash):
            return otp_record

        otp_record.attempt_count += 1
        if otp_record.attempt_count >= self.settings.otp_max_attempts:
            otp_record.used_at = now
        await self.session.commit()
        raise TokenValidationError

    async def register(self, payload: RegisterRequest) -> User:
        existing_user = await self._get_user_by_email(payload.email)
        if existing_user:
            raise DuplicateEmailError

        user = User(
            email=normalize_email(str(payload.email)),
            full_name=payload.full_name,
            password_hash=await asyncify(hash_password)(payload.password),
            industry=payload.industry,
            job_role=payload.job_role,
            terms_accepted_at=utc_now(),
            terms_version=self.settings.terms_version,
        )
        self.session.add(user)
        try:
            # The unique-email constraint can fail at flush time when two registrations
            # for the same normalized address race. Translate both flush- and
            # commit-time constraint failures into the public 409 response.
            await self.session.flush()
            raw_otp = await self._issue_otp(
                user=user,
                otp_model=EmailVerificationOTP,
                expires_in_minutes=self.settings.email_verification_expire_minutes,
                enforce_resend_cooldown=False,
            )
            if raw_otp is None:
                raise RuntimeError("Initial verification OTP issuance unexpectedly failed")
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise DuplicateEmailError from exc

        try:
            await self.email_sender.send_verification_otp(user.email, raw_otp)
        except Exception:
            logger.exception("Verification OTP delivery failed", extra={"user_id": str(user.id)})

        await self.session.refresh(user)
        return user

    async def resend_verification(self, email: str) -> None:
        user = await self._get_user_by_email(email, lock_for_otp=True)
        if not user or user.is_verified or not user.is_active:
            return

        raw_otp = await self._issue_otp(
            user=user,
            otp_model=EmailVerificationOTP,
            expires_in_minutes=self.settings.email_verification_expire_minutes,
            enforce_resend_cooldown=True,
        )
        if raw_otp is None:
            return
        await self.session.commit()
        try:
            await self.email_sender.send_verification_otp(user.email, raw_otp)
        except Exception:
            logger.exception("Verification OTP resend failed", extra={"user_id": str(user.id)})

    async def verify_email(self, payload: VerifyEmailOTPRequest) -> None:
        user = await self._get_user_by_email(str(payload.email), lock_for_otp=True)
        if not user or not user.is_active or user.is_verified:
            await asyncify(verify_otp_against_dummy)(payload.otp)
            raise TokenValidationError

        otp_record = await self._get_active_otp(EmailVerificationOTP, user.id)
        otp_record = await self._validate_otp(otp_record, payload.otp)
        user.is_verified = True
        otp_record.used_at = utc_now()
        await self.session.commit()

    async def authenticate(self, payload: LoginRequest) -> User:
        user = await self._get_user_by_email(payload.email)
        if not user:
            await asyncify(verify_password_against_dummy)(payload.password)
            raise AuthenticationError
        if not await asyncify(verify_password)(payload.password, user.password_hash):
            raise AuthenticationError
        if not user.is_active:
            raise InactiveAccountError
        if not user.is_verified:
            raise EmailVerificationRequiredError

        user.last_login_at = utc_now()
        await self.session.commit()
        return user

    async def _create_token_pair(self, user: User, *, remember_me: bool) -> SessionTokens:
        access = create_jwt_token(
            user_id=user.id,
            token_type=TokenType.ACCESS,
            settings=self.settings,
            expires_delta=timedelta(minutes=self.settings.access_token_expire_minutes),
        )
        refresh = create_jwt_token(
            user_id=user.id,
            token_type=TokenType.REFRESH,
            settings=self.settings,
            expires_delta=timedelta(days=self.settings.refresh_token_expire_days),
        )
        self.session.add(
            RefreshToken(
                user_id=user.id,
                token_id=refresh.token_id,
                token_hash=hash_opaque_token(refresh.raw_token),
                expires_at=refresh.expires_at,
                is_persistent=remember_me,
            )
        )
        await self.session.commit()
        return SessionTokens(
            access=AccessTokenResponse(
                access_token=access.raw_token,
                access_token_expires_in=self.settings.access_token_expire_minutes * 60,
            ),
            refresh_token=refresh.raw_token,
            remember_me=remember_me,
        )

    async def login(self, payload: LoginRequest) -> SessionTokens:
        user = await self.authenticate(payload)
        return await self._create_token_pair(user, remember_me=payload.remember_me)

    async def refresh(self, raw_token: str) -> SessionTokens:
        try:
            claims = decode_jwt_token(raw_token, self.settings)
        except ValueError as exc:
            raise TokenValidationError from exc
        if claims.token_type is not TokenType.REFRESH:
            raise TokenValidationError

        result = await self.session.execute(
            select(RefreshToken).where(RefreshToken.token_id == claims.token_id).with_for_update()
        )
        stored_token = result.scalar_one_or_none()
        if (
            not stored_token
            or stored_token.revoked_at
            or stored_token.expires_at <= utc_now()
            or stored_token.token_hash != hash_opaque_token(raw_token)
        ):
            raise TokenValidationError

        user = await self._get_user_by_id(claims.user_id)
        if not user or not user.is_active or not user.is_verified:
            raise TokenValidationError

        stored_token.revoked_at = utc_now()
        return await self._create_token_pair(
            user,
            remember_me=stored_token.is_persistent,
        )

    async def logout(self, raw_token: str) -> None:
        try:
            claims = decode_jwt_token(raw_token, self.settings)
        except ValueError:
            return
        if claims.token_type is not TokenType.REFRESH:
            return

        result = await self.session.execute(
            select(RefreshToken).where(RefreshToken.token_id == claims.token_id).with_for_update()
        )
        stored_token = result.scalar_one_or_none()
        if stored_token and not stored_token.revoked_at:
            stored_token.revoked_at = utc_now()
            await self.session.commit()

    async def request_password_reset(self, payload: ForgotPasswordRequest) -> None:
        user = await self._get_user_by_email(payload.email, lock_for_otp=True)
        if not user or not user.is_active or not user.is_verified:
            return
        raw_otp = await self._issue_otp(
            user=user,
            otp_model=PasswordResetOTP,
            expires_in_minutes=self.settings.password_reset_expire_minutes,
            enforce_resend_cooldown=True,
        )
        if raw_otp is None:
            return
        await self.session.commit()
        try:
            await self.email_sender.send_password_reset_otp(user.email, raw_otp)
        except Exception:
            logger.exception("Password-reset OTP delivery failed", extra={"user_id": str(user.id)})

    async def verify_password_reset_otp(self, payload: VerifyPasswordResetOTPRequest) -> None:
        user = await self._get_user_by_email(str(payload.email), lock_for_otp=True)
        if not user or not user.is_active or not user.is_verified:
            await asyncify(verify_otp_against_dummy)(payload.otp)
            raise TokenValidationError

        otp_record = await self._get_active_otp(PasswordResetOTP, user.id)
        otp_record = await self._validate_otp(otp_record, payload.otp)
        if not isinstance(otp_record, PasswordResetOTP):
            raise TokenValidationError
        otp_record.verified_at = utc_now()
        await self.session.commit()

    async def reset_password(self, payload: ResetPasswordRequest) -> None:
        user = await self._get_user_by_email(str(payload.email), lock_for_otp=True)
        if not user or not user.is_active or not user.is_verified:
            await asyncify(verify_otp_against_dummy)(payload.otp)
            raise TokenValidationError

        reset_otp = await self._get_active_otp(PasswordResetOTP, user.id)
        reset_otp = await self._validate_otp(reset_otp, payload.otp)
        if not isinstance(reset_otp, PasswordResetOTP) or not reset_otp.verified_at:
            raise TokenValidationError

        user.password_hash = await asyncify(hash_password)(payload.new_password)
        reset_otp.used_at = utc_now()
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )
        await self.session.commit()
