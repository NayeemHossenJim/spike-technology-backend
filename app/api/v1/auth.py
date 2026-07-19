from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.deps import enforce_auth_rate_limit_dependency, get_auth_service
from app.core.config import Settings, get_settings
from app.core.session_cookie import (
    REFRESH_COOKIE_NAME,
    clear_refresh_cookie,
    set_refresh_cookie,
)
from app.schemas.auth import (
    AccessTokenResponse,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResetPasswordRequest,
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
SettingsDep = Annotated[Settings, Depends(get_settings)]


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


@router.post("/login", response_model=AccessTokenResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    _: RateLimitDep,
) -> AccessTokenResponse:
    try:
        tokens = await service.login(payload)
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
    set_refresh_cookie(
        response,
        refresh_token=tokens.refresh_token,
        remember_me=tokens.remember_me,
        settings=settings,
    )
    return tokens.access


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(
    request: Request,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    _: RateLimitDep,
) -> AccessTokenResponse:
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh-token cookie is missing.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        tokens = await service.refresh(refresh_token)
    except TokenValidationError as exc:
        clear_refresh_cookie(response, settings=settings)
        headers = {"WWW-Authenticate": "Bearer"}
        if expired_cookie := response.headers.get("set-cookie"):
            headers["Set-Cookie"] = expired_cookie
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
            headers=headers,
        ) from exc
    set_refresh_cookie(
        response,
        refresh_token=tokens.refresh_token,
        remember_me=tokens.remember_me,
        settings=settings,
    )
    return tokens.access


@router.post("/logout", response_model=MessageResponse, status_code=status.HTTP_202_ACCEPTED)
async def logout(
    request: Request,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    _: RateLimitDep,
) -> MessageResponse:
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if refresh_token:
        await service.logout(refresh_token)
    clear_refresh_cookie(response, settings=settings)
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
