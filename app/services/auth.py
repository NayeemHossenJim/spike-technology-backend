from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.config import Settings
from app.core.security import (
    TokenType,
    create_jwt_token,
    create_opaque_token,
    decode_jwt_token,
    hash_opaque_token,
    hash_password,
    utc_now,
    verify_password,
    verify_password_against_dummy,
)
from app.models.auth import EmailVerificationToken, PasswordResetToken, RefreshToken
from app.models.user import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenPairResponse,
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


def normalize_email(email: str) -> str:
    return email.strip().lower()


class AuthService:
    def __init__(
        self, session: AsyncSession, settings: Settings, email_sender: EmailSender
    ) -> None:
        self.session = session
        self.settings = settings
        self.email_sender = email_sender

    async def _get_user_by_email(self, email: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.email == normalize_email(email))
        )
        return result.scalar_one_or_none()

    async def _get_user_by_id(self, user_id: UUID) -> User | None:
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def _issue_email_verification_token(self, user: User) -> str:
        await self.session.execute(
            update(EmailVerificationToken)
            .where(
                EmailVerificationToken.user_id == user.id,
                EmailVerificationToken.used_at.is_(None),
            )
            .values(used_at=utc_now())
        )
        raw_token = create_opaque_token()
        token = EmailVerificationToken(
            user_id=user.id,
            token_hash=hash_opaque_token(raw_token),
            expires_at=utc_now()
            + timedelta(minutes=self.settings.email_verification_expire_minutes),
        )
        self.session.add(token)
        return raw_token

    async def _issue_password_reset_token(self, user: User) -> str:
        await self.session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=utc_now())
        )
        raw_token = create_opaque_token()
        token = PasswordResetToken(
            user_id=user.id,
            token_hash=hash_opaque_token(raw_token),
            expires_at=utc_now() + timedelta(minutes=self.settings.password_reset_expire_minutes),
        )
        self.session.add(token)
        return raw_token

    async def register(self, payload: RegisterRequest) -> User:
        existing_user = await self._get_user_by_email(payload.email)
        if existing_user:
            raise DuplicateEmailError

        user = User(
            email=normalize_email(str(payload.email)),
            full_name=payload.full_name.strip(),
            password_hash=hash_password(payload.password),
            industry=payload.industry.strip() if payload.industry else None,
            job_title=payload.job_title.strip() if payload.job_title else None,
        )
        self.session.add(user)
        await self.session.flush()
        raw_token = await self._issue_email_verification_token(user)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise DuplicateEmailError from exc

        try:
            await self.email_sender.send_verification_email(user.email, raw_token)
        except Exception:
            logger.exception("Verification email delivery failed", extra={"user_id": str(user.id)})

        await self.session.refresh(user)
        return user

    async def resend_verification(self, email: str) -> None:
        user = await self._get_user_by_email(email)
        if not user or user.is_verified or not user.is_active:
            return

        raw_token = await self._issue_email_verification_token(user)
        await self.session.commit()
        try:
            await self.email_sender.send_verification_email(user.email, raw_token)
        except Exception:
            logger.exception("Verification email resend failed", extra={"user_id": str(user.id)})

    async def verify_email(self, raw_token: str) -> None:
        token_hash = hash_opaque_token(raw_token)
        result = await self.session.execute(
            select(EmailVerificationToken).where(EmailVerificationToken.token_hash == token_hash)
        )
        token = result.scalar_one_or_none()
        if not token or token.used_at or token.expires_at <= utc_now():
            raise TokenValidationError

        user = await self._get_user_by_id(token.user_id)
        if not user or not user.is_active:
            raise TokenValidationError

        user.is_verified = True
        token.used_at = utc_now()
        await self.session.commit()

    async def authenticate(self, payload: LoginRequest) -> User:
        user = await self._get_user_by_email(payload.email)
        if not user:
            verify_password_against_dummy(payload.password)
            raise AuthenticationError
        if not verify_password(payload.password, user.password_hash):
            raise AuthenticationError
        if not user.is_active:
            raise InactiveAccountError
        if not user.is_verified:
            raise EmailVerificationRequiredError

        user.last_login_at = utc_now()
        await self.session.commit()
        return user

    async def _create_token_pair(self, user: User) -> TokenPairResponse:
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
            )
        )
        await self.session.commit()
        return TokenPairResponse(
            access_token=access.raw_token,
            refresh_token=refresh.raw_token,
            access_token_expires_in=self.settings.access_token_expire_minutes * 60,
        )

    async def login(self, payload: LoginRequest) -> TokenPairResponse:
        user = await self.authenticate(payload)
        return await self._create_token_pair(user)

    async def refresh(self, raw_token: str) -> TokenPairResponse:
        try:
            claims = decode_jwt_token(raw_token, self.settings)
        except ValueError as exc:
            raise TokenValidationError from exc
        if claims.token_type is not TokenType.REFRESH:
            raise TokenValidationError

        result = await self.session.execute(
            select(RefreshToken).where(RefreshToken.token_id == claims.token_id)
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
        return await self._create_token_pair(user)

    async def logout(self, raw_token: str) -> None:
        try:
            claims = decode_jwt_token(raw_token, self.settings)
        except ValueError:
            return
        if claims.token_type is not TokenType.REFRESH:
            return

        result = await self.session.execute(
            select(RefreshToken).where(RefreshToken.token_id == claims.token_id)
        )
        stored_token = result.scalar_one_or_none()
        if stored_token and not stored_token.revoked_at:
            stored_token.revoked_at = utc_now()
            await self.session.commit()

    async def request_password_reset(self, payload: ForgotPasswordRequest) -> None:
        user = await self._get_user_by_email(payload.email)
        if not user or not user.is_active or not user.is_verified:
            return
        raw_token = await self._issue_password_reset_token(user)
        await self.session.commit()
        try:
            await self.email_sender.send_password_reset_email(user.email, raw_token)
        except Exception:
            logger.exception(
                "Password reset email delivery failed", extra={"user_id": str(user.id)}
            )

    async def reset_password(self, payload: ResetPasswordRequest) -> None:
        token_hash = hash_opaque_token(payload.token)
        result = await self.session.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
        reset_token = result.scalar_one_or_none()
        if not reset_token or reset_token.used_at or reset_token.expires_at <= utc_now():
            raise TokenValidationError

        user = await self._get_user_by_id(reset_token.user_id)
        if not user or not user.is_active:
            raise TokenValidationError

        user.password_hash = hash_password(payload.new_password)
        reset_token.used_at = utc_now()
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )
        await self.session.commit()
