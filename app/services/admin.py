from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import func, select

from app.models.business import Business, RoleAssignment
from app.models.user import User, UserRole


@dataclass(frozen=True, slots=True)
class AdminUserPage:
    items: tuple[User, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class AdminBusinessRecord:
    business: Business
    owner: User
    role_assignment: RoleAssignment


@dataclass(frozen=True, slots=True)
class AdminBusinessPage:
    items: tuple[AdminBusinessRecord, ...]
    total: int
    limit: int
    offset: int


def normalize_admin_search(query: str | None) -> str | None:
    if query is None:
        return None

    normalized = query.strip()

    if not normalized:
        return None

    return normalized


class AdminService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_users(
        self,
        *,
        query: str | None,
        limit: int,
        offset: int,
        include_platform_roles: bool,
    ) -> AdminUserPage:
        criteria = []

        if not include_platform_roles:
            criteria.append(User.role == UserRole.USER)

        normalized_query = normalize_admin_search(query)

        if normalized_query is not None:
            pattern = f"%{normalized_query}%"
            criteria.append(
                or_(
                    User.email.ilike(pattern),
                    User.full_name.ilike(pattern),
                )
            )

        count_statement = select(func.count()).select_from(User)

        if criteria:
            count_statement = count_statement.where(*criteria)

        total = await self.session.scalar(count_statement)

        statement = select(User)

        if criteria:
            statement = statement.where(*criteria)

        statement = (
            statement.order_by(
                User.created_at.desc(),
                User.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        result = await self.session.execute(statement)

        return AdminUserPage(
            items=tuple(result.scalars().all()),
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

    async def list_businesses(
        self,
        *,
        query: str | None,
        limit: int,
        offset: int,
    ) -> AdminBusinessPage:
        normalized_query = normalize_admin_search(query)

        count_statement = (
            select(func.count()).select_from(Business).join(User, User.id == Business.owner_user_id)
        )

        statement = (
            select(Business, User, RoleAssignment)
            .join(User, User.id == Business.owner_user_id)
            .join(
                RoleAssignment,
                and_(
                    RoleAssignment.business_id == Business.id,
                    RoleAssignment.user_id == Business.owner_user_id,
                ),
            )
        )

        if normalized_query is not None:
            pattern = f"%{normalized_query}%"
            search_filter = or_(
                Business.name.ilike(pattern),
                User.email.ilike(pattern),
                User.full_name.ilike(pattern),
            )

            count_statement = count_statement.where(search_filter)
            statement = statement.where(search_filter)

        total = await self.session.scalar(count_statement)

        result = await self.session.execute(
            statement.order_by(
                Business.created_at.desc(),
                Business.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        items = tuple(
            AdminBusinessRecord(
                business=business,
                owner=owner,
                role_assignment=role_assignment,
            )
            for business, owner, role_assignment in result.all()
        )

        return AdminBusinessPage(
            items=items,
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )
