from __future__ import annotations

from starlette.responses import Response

from app.core.config import AppEnvironment, Settings

REFRESH_COOKIE_NAME = "spike_refresh_token"


def _cookie_path(settings: Settings) -> str:
    return f"{settings.api_v1_prefix}/auth"


def set_refresh_cookie(
    response: Response,
    *,
    refresh_token: str,
    remember_me: bool,
    settings: Settings,
) -> None:
    """Store the refresh token where browser JavaScript cannot access it."""

    max_age = settings.refresh_token_expire_days * 24 * 60 * 60 if remember_me else None
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=max_age,
        httponly=True,
        secure=settings.app_env is AppEnvironment.PRODUCTION,
        samesite="lax",
        path=_cookie_path(settings),
    )


def clear_refresh_cookie(response: Response, *, settings: Settings) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        httponly=True,
        secure=settings.app_env is AppEnvironment.PRODUCTION,
        samesite="lax",
        path=_cookie_path(settings),
    )
