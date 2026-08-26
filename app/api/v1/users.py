from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    enforce_auth_rate_limit_dependency,
    get_account_security_service,
    get_current_user,
    get_email_two_step_service,
)
from app.core.config import Settings, get_settings
from app.core.session_cookie import clear_refresh_cookie
from app.db.session import get_session
from app.models.auth import EmailTwoStepPurpose
from app.models.user import User
from app.schemas.auth import EmailTwoStepChallengeResponse, MessageResponse, UserRead
from app.schemas.user import (
    UserAccountDeleteRequest,
    UserEmailChangeConfirm,
    UserEmailChangeRequest,
    UserEmailTwoStepChallengeRequest,
    UserEmailTwoStepConfirmRequest,
    UserEmailTwoStepDisableRequest,
    UserEmailTwoStepEnableRequest,
    UserEmailTwoStepStatusResponse,
    UserPasswordChangeRequest,
    UserProfileUpdate,
)
from app.services.account_security import (
    AccountDeletionBillingConflictError,
    AccountSecurityNotFoundError,
    AccountSecurityService,
    CurrentEmailMismatchError,
    CurrentPasswordInvalidError,
    EmailChangeConflictError,
    EmailChangeOTPInvalidError,
    LastSuperAdminDeletionError,
    PasswordReuseError,
)
from app.services.email_two_step import (
    EmailTwoStepChallengeInvalidError,
    EmailTwoStepEmailMismatchError,
    EmailTwoStepNotFoundError,
    EmailTwoStepPasswordInvalidError,
    EmailTwoStepService,
    EmailTwoStepStateConflictError,
)
from app.services.user_profiles import (
    UserProfileConflictError,
    UserProfileNotFoundError,
    UserProfileService,
)

router = APIRouter(prefix="/users", tags=["Users"])
AccountSecurityServiceDep = Annotated[
    AccountSecurityService,
    Depends(get_account_security_service),
]
EmailTwoStepServiceDep = Annotated[
    EmailTwoStepService,
    Depends(get_email_two_step_service),
]
RateLimitDep = Annotated[None, Depends(enforce_auth_rate_limit_dependency)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: Annotated[User, Depends(get_current_user)]) -> UserRead:
    return UserRead.model_validate(current_user)


@router.patch(
    "/me",
    response_model=UserRead,
    responses={
        409: {"description": "Profile persistence conflict."},
    },
)
async def update_current_user_profile(
    payload: UserProfileUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserRead:
    try:
        user = await UserProfileService(session).update(
            current_user.id,
            payload=payload,
        )
    except UserProfileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found.",
        ) from exc
    except UserProfileConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The profile update conflicts with persisted account data.",
        ) from exc

    return UserRead.model_validate(user)


def _raise_current_password_invalid(exc: CurrentPasswordInvalidError) -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="The current password is incorrect.",
        headers={"WWW-Authenticate": "Bearer"},
    ) from exc


def _raise_email_two_step_password_invalid(
    exc: EmailTwoStepPasswordInvalidError,
) -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="The current password is incorrect.",
        headers={"WWW-Authenticate": "Bearer"},
    ) from exc


def _raise_email_two_step_challenge_invalid(exc: Exception) -> None:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="The email verification challenge or code is invalid or has expired.",
    ) from exc


@router.get(
    "/me/email-two-step",
    response_model=UserEmailTwoStepStatusResponse,
)
async def read_current_user_email_two_step_status(
    current_user: Annotated[User, Depends(get_current_user)],
    service: EmailTwoStepServiceDep,
) -> UserEmailTwoStepStatusResponse:
    try:
        return await service.get_status(current_user.id)
    except EmailTwoStepNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account not found.",
        ) from exc


@router.post(
    "/me/email-two-step/enable",
    response_model=EmailTwoStepChallengeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        401: {"description": "Current password is incorrect."},
        409: {"description": "Email or two-step state conflicts with the account."},
    },
)
async def request_current_user_email_two_step_enable(
    payload: UserEmailTwoStepEnableRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    service: EmailTwoStepServiceDep,
    _: RateLimitDep,
) -> EmailTwoStepChallengeResponse:
    try:
        return await service.request_enable(
            current_user.id,
            email=str(payload.email),
            current_password=payload.current_password,
        )
    except EmailTwoStepPasswordInvalidError as exc:
        _raise_email_two_step_password_invalid(exc)
    except EmailTwoStepEmailMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The email address does not match the current account email.",
        ) from exc
    except EmailTwoStepStateConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email verification is already enabled.",
        ) from exc
    except EmailTwoStepNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account not found.",
        ) from exc


@router.post(
    "/me/email-two-step/enable/confirm",
    response_model=UserEmailTwoStepStatusResponse,
    responses={400: {"description": "Challenge or OTP is invalid or expired."}},
)
async def confirm_current_user_email_two_step_enable(
    payload: UserEmailTwoStepConfirmRequest,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    service: EmailTwoStepServiceDep,
    settings: SettingsDep,
    _: RateLimitDep,
) -> UserEmailTwoStepStatusResponse:
    try:
        result = await service.confirm_enable(
            current_user.id,
            payload.challenge_id,
            raw_otp=payload.otp,
        )
    except (EmailTwoStepChallengeInvalidError, EmailTwoStepNotFoundError) as exc:
        _raise_email_two_step_challenge_invalid(exc)
    except EmailTwoStepStateConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email verification is already enabled.",
        ) from exc
    clear_refresh_cookie(response, settings=settings)
    return result


@router.post(
    "/me/email-two-step/disable",
    response_model=EmailTwoStepChallengeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        401: {"description": "Current password is incorrect."},
        409: {"description": "Email verification is not enabled."},
    },
)
async def request_current_user_email_two_step_disable(
    payload: UserEmailTwoStepDisableRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    service: EmailTwoStepServiceDep,
    _: RateLimitDep,
) -> EmailTwoStepChallengeResponse:
    try:
        return await service.request_disable(
            current_user.id,
            current_password=payload.current_password,
        )
    except EmailTwoStepPasswordInvalidError as exc:
        _raise_email_two_step_password_invalid(exc)
    except EmailTwoStepStateConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email verification is not enabled.",
        ) from exc
    except EmailTwoStepNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account not found.",
        ) from exc


@router.post(
    "/me/email-two-step/disable/confirm",
    response_model=UserEmailTwoStepStatusResponse,
    responses={400: {"description": "Challenge or OTP is invalid or expired."}},
)
async def confirm_current_user_email_two_step_disable(
    payload: UserEmailTwoStepConfirmRequest,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    service: EmailTwoStepServiceDep,
    settings: SettingsDep,
    _: RateLimitDep,
) -> UserEmailTwoStepStatusResponse:
    try:
        result = await service.confirm_disable(
            current_user.id,
            payload.challenge_id,
            raw_otp=payload.otp,
        )
    except (EmailTwoStepChallengeInvalidError, EmailTwoStepNotFoundError) as exc:
        _raise_email_two_step_challenge_invalid(exc)
    except EmailTwoStepStateConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email verification is not enabled.",
        ) from exc
    clear_refresh_cookie(response, settings=settings)
    return result


@router.post(
    "/me/email-two-step/resend",
    response_model=EmailTwoStepChallengeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={400: {"description": "Challenge is invalid or expired."}},
)
async def resend_current_user_email_two_step_challenge(
    payload: UserEmailTwoStepChallengeRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    service: EmailTwoStepServiceDep,
    _: RateLimitDep,
) -> EmailTwoStepChallengeResponse:
    try:
        return await service.resend(
            payload.challenge_id,
            allowed_purposes=(
                EmailTwoStepPurpose.ENABLE,
                EmailTwoStepPurpose.DISABLE,
            ),
            expected_user_id=current_user.id,
        )
    except (EmailTwoStepChallengeInvalidError, EmailTwoStepNotFoundError) as exc:
        _raise_email_two_step_challenge_invalid(exc)


@router.post(
    "/me/email-change",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        401: {"description": "Current password is incorrect."},
        409: {"description": "Current or requested email conflicts with account state."},
    },
)
async def request_current_user_email_change(
    payload: UserEmailChangeRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    service: AccountSecurityServiceDep,
    _: RateLimitDep,
) -> MessageResponse:
    try:
        await service.request_email_change(current_user.id, payload=payload)
    except CurrentPasswordInvalidError as exc:
        _raise_current_password_invalid(exc)
    except CurrentEmailMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The old email address does not match the current account email.",
        ) from exc
    except EmailChangeConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The requested email address cannot be used.",
        ) from exc
    except AccountSecurityNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account not found.",
        ) from exc

    return MessageResponse(
        message="If eligible, a verification code was sent to the new email address."
    )


@router.post(
    "/me/email-change/confirm",
    response_model=MessageResponse,
    responses={
        400: {"description": "Email-change OTP is invalid or expired."},
        409: {"description": "Requested email conflicts with account state."},
    },
)
async def confirm_current_user_email_change(
    payload: UserEmailChangeConfirm,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    service: AccountSecurityServiceDep,
    settings: SettingsDep,
    _: RateLimitDep,
) -> MessageResponse:
    try:
        await service.confirm_email_change(current_user.id, payload=payload)
    except EmailChangeOTPInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The email-change OTP is invalid or has expired.",
        ) from exc
    except EmailChangeConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The requested email address cannot be used.",
        ) from exc
    except AccountSecurityNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account not found.",
        ) from exc

    clear_refresh_cookie(response, settings=settings)
    return MessageResponse(message="Email changed successfully. Please sign in again.")


@router.put(
    "/me/password",
    response_model=MessageResponse,
    responses={
        401: {"description": "Current password is incorrect."},
        409: {"description": "New password reuses the current password."},
    },
)
async def change_current_user_password(
    payload: UserPasswordChangeRequest,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    service: AccountSecurityServiceDep,
    settings: SettingsDep,
    _: RateLimitDep,
) -> MessageResponse:
    try:
        await service.change_password(current_user.id, payload=payload)
    except CurrentPasswordInvalidError as exc:
        _raise_current_password_invalid(exc)
    except PasswordReuseError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The new password must differ from the current password.",
        ) from exc
    except AccountSecurityNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account not found.",
        ) from exc

    clear_refresh_cookie(response, settings=settings)
    return MessageResponse(message="Password changed successfully. Please sign in again.")


@router.delete(
    "/me",
    response_model=MessageResponse,
    responses={
        401: {"description": "Current password is incorrect."},
        409: {"description": "Billing or platform-administration state blocks deletion."},
    },
)
async def delete_current_user_account(
    payload: UserAccountDeleteRequest,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    service: AccountSecurityServiceDep,
    settings: SettingsDep,
    _: RateLimitDep,
) -> MessageResponse:
    try:
        await service.delete_account(current_user.id, payload=payload)
    except CurrentPasswordInvalidError as exc:
        _raise_current_password_invalid(exc)
    except AccountDeletionBillingConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Finish or cancel the pending checkout and allow any Stripe-managed "
                "subscription to end before deleting the account."
            ),
        ) from exc
    except LastSuperAdminDeletionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The last active super administrator cannot delete their account.",
        ) from exc
    except AccountSecurityNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account not found.",
        ) from exc

    clear_refresh_cookie(response, settings=settings)
    return MessageResponse(message="Account deleted successfully.")
