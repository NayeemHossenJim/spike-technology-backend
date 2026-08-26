from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_session
from app.models.user import User
from app.schemas.auth import UserRead
from app.schemas.user import UserProfileUpdate
from app.services.user_profiles import (
    UserProfileConflictError,
    UserProfileNotFoundError,
    UserProfileService,
)

router = APIRouter(prefix="/users", tags=["Users"])


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
