from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.business import Business
from app.models.user import User
from app.schemas.user import UserProfileUpdate


class UserProfileNotFoundError(Exception):
    pass


class UserProfileConflictError(Exception):
    pass


class UserProfileService:
    """Serialize self-service profile updates and preserve industry consistency."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def update(
        self,
        user_id: UUID,
        *,
        payload: UserProfileUpdate,
    ) -> User:
        try:
            result = await self.session.execute(
                select(User).where(User.id == user_id).with_for_update()
            )
            user = result.scalar_one_or_none()
            if user is None:
                raise UserProfileNotFoundError

            values = payload.model_dump(exclude_unset=True)
            for field, value in values.items():
                setattr(user, field, value)

            if "industry" in values:
                business_result = await self.session.execute(
                    select(Business).where(Business.owner_user_id == user.id).with_for_update()
                )
                business = business_result.scalar_one_or_none()
                if business is not None:
                    # Business industry drives dashboard/PDF presentation while
                    # the settings screen edits the user's industry. Keep the
                    # two copies aligned in the same transaction.
                    business.industry = payload.industry

            await self.session.commit()
            await self.session.refresh(user)
            return user
        except IntegrityError as exc:
            await self.session.rollback()
            raise UserProfileConflictError from exc
        except Exception:
            await self.session.rollback()
            raise
