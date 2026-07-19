from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import enforce_auth_rate_limit_dependency, get_auth_service
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenPairResponse,
    UserRead,
    VerifyEmailOTPRequest,
    VerifyPasswordResetOTPRequest,
)
from app.services.auth import (
    AuthenticationError,
    AuthService,
    DuplicateEmailError,
    EmailVerificationRequiredError,
    InactiveAccountError,
    TokenValidationError,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
RateLimitDep = Annotated[None, Depends(enforce_auth_rate_limit_dependency)]


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, service: AuthServiceDep, _: RateLimitDep) -> UserRead:
    try:
        user = await service.register(payload)
    except DuplicateEmailError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        ) from exc
    return UserRead.model_validate(user)


@router.post("/verify-email", response_model=MessageResponse)
async def verify_email(
    payload: VerifyEmailOTPRequest, service: AuthServiceDep, _: RateLimitDep
) -> MessageResponse:
    try:
        await service.verify_email(payload)
    except TokenValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The verification OTP is invalid or has expired.",
        ) from exc
    return MessageResponse(message="Email verified successfully.")


@router.post(
    "/resend-verification", response_model=MessageResponse, status_code=status.HTTP_202_ACCEPTED
)
async def resend_verification(
    payload: ForgotPasswordRequest,
    service: AuthServiceDep,
    _: RateLimitDep,
) -> MessageResponse:
    await service.resend_verification(str(payload.email))
    return MessageResponse(
        message="If an eligible account exists, a verification email will be sent."
    )


@router.post("/login", response_model=TokenPairResponse)
async def login(
    payload: LoginRequest, service: AuthServiceDep, _: RateLimitDep
) -> TokenPairResponse:
    try:
        return await service.login(payload)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except EmailVerificationRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email before signing in.",
        ) from exc
    except InactiveAccountError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is inactive.",
        ) from exc


@router.post("/refresh", response_model=TokenPairResponse)
async def refresh(
    payload: RefreshRequest, service: AuthServiceDep, _: RateLimitDep
) -> TokenPairResponse:
    try:
        return await service.refresh(payload.refresh_token)
    except TokenValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


@router.post("/logout", response_model=MessageResponse, status_code=status.HTTP_202_ACCEPTED)
async def logout(
    payload: LogoutRequest, service: AuthServiceDep, _: RateLimitDep
) -> MessageResponse:
    await service.logout(payload.refresh_token)
    return MessageResponse(message="Signed out successfully.")


@router.post(
    "/forgot-password", response_model=MessageResponse, status_code=status.HTTP_202_ACCEPTED
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    service: AuthServiceDep,
    _: RateLimitDep,
) -> MessageResponse:
    await service.request_password_reset(payload)
    return MessageResponse(
        message="If an eligible account exists, a password-reset OTP will be sent."
    )


@router.post("/verify-password-reset-otp", response_model=MessageResponse)
async def verify_password_reset_otp(
    payload: VerifyPasswordResetOTPRequest, service: AuthServiceDep, _: RateLimitDep
) -> MessageResponse:
    try:
        await service.verify_password_reset_otp(payload)
    except TokenValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The password-reset OTP is invalid or has expired.",
        ) from exc
    return MessageResponse(message="Password-reset OTP verified successfully.")


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    payload: ResetPasswordRequest,
    service: AuthServiceDep,
    _: RateLimitDep,
) -> MessageResponse:
    try:
        await service.reset_password(payload)
    except TokenValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The password-reset OTP is invalid, expired, or has not been verified.",
        ) from exc
    return MessageResponse(message="Password reset successfully. Please sign in again.")
