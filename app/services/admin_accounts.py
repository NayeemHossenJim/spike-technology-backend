from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.auth import RefreshToken
from app.models.base import utc_now
from app.models.business import Business
from app.models.user import User, UserRole
from app.schemas.admin import AdminAccountActionReason
from app.services.admin_audit import AdminAuditService


class AdminAccountError(Exception):
    pass


class AdminAccountActorForbiddenError(AdminAccountError):
    pass


class AdminAccountTargetNotFoundError(AdminAccountError):
    pass


class AdminAccountTargetForbiddenError(AdminAccountError):
    pass


@dataclass(frozen=True, slots=True)
class AdminAccountActionResult:
    user: User
    changed: bool
    sessions_revoked: int


class AdminAccountService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _validate_actor(actor: User) -> None:
        if actor.role != UserRole.SUPER_ADMIN or not actor.is_active or not actor.is_verified:
            raise AdminAccountActorForbiddenError

    async def _change_active_state(
        self,
        *,
        actor: User,
        target_user_id: UUID,
        desired_active: bool,
        reason_code: AdminAccountActionReason,
        request_id: str,
    ) -> AdminAccountActionResult:
        self._validate_actor(actor)

        try:
            result = await self.session.execute(
                select(User).where(User.id == target_user_id).with_for_update()
            )
            target = result.scalar_one_or_none()

            if target is None:
                raise AdminAccountTargetNotFoundError

            if target.id == actor.id or target.role != UserRole.USER:
                raise AdminAccountTargetForbiddenError

            business_id = await self.session.scalar(
                select(Business.id).where(Business.owner_user_id == target.id)
            )

            previous_is_active = target.is_active
            changed = previous_is_active != desired_active
            sessions_revoked = 0

            if changed:
                target.is_active = desired_active

            if not desired_active:
                if changed:
                    # Permanently invalidates all previously issued access JWTs.
                    target.auth_session_version += 1

                revocation = await self.session.execute(
                    update(RefreshToken)
                    .where(
                        RefreshToken.user_id == target.id,
                        RefreshToken.revoked_at.is_(None),
                    )
                    .values(revoked_at=utc_now())
                )
                sessions_revoked = int(revocation.rowcount or 0)

            await AdminAuditService(self.session).record(
                actor=actor,
                action=("user.reactivate" if desired_active else "user.suspend"),
                target_type="user",
                target_id=target.id,
                business_id=business_id,
                request_id=request_id,
                metadata={
                    "reason_code": reason_code.value,
                    "previous_is_active": previous_is_active,
                    "current_is_active": desired_active,
                    "changed": changed,
                    "sessions_revoked": sessions_revoked,
                },
            )

            await self.session.commit()
            await self.session.refresh(target)

            return AdminAccountActionResult(
                user=target,
                changed=changed,
                sessions_revoked=sessions_revoked,
            )
        except Exception:
            await self.session.rollback()
            raise

    async def suspend_user(
        self,
        *,
        actor: User,
        target_user_id: UUID,
        reason_code: AdminAccountActionReason,
        request_id: str,
    ) -> AdminAccountActionResult:
        return await self._change_active_state(
            actor=actor,
            target_user_id=target_user_id,
            desired_active=False,
            reason_code=reason_code,
            request_id=request_id,
        )

    async def reactivate_user(
        self,
        *,
        actor: User,
        target_user_id: UUID,
        reason_code: AdminAccountActionReason,
        request_id: str,
    ) -> AdminAccountActionResult:
        return await self._change_active_state(
            actor=actor,
            target_user_id=target_user_id,
            desired_active=True,
            reason_code=reason_code,
            request_id=request_id,
        )
