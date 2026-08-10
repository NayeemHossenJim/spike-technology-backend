from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.models.admin import AdminAuditEvent
from app.models.user import User, UserRole
from app.services.admin_audit import AdminAuditService


async def create_operator(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    email: str,
    role: UserRole,
) -> User:
    async with session_factory() as session:
        user = User(
            email=email,
            full_name="M7 Audit Operator",
            password_hash="unused",
            role=role,
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        session.expunge(user)
        return user


@pytest.mark.integration
async def test_admin_audit_event_persists_safe_server_side_context(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    actor = await create_operator(
        session_factory,
        email="m7-audit-admin@example.com",
        role=UserRole.SUPER_ADMIN,
    )
    target_id = uuid4()
    business_id = uuid4()

    async with session_factory() as session:
        event = await AdminAuditService(session).record(
            actor=actor,
            action="user.inspect",
            target_type="user",
            target_id=target_id,
            business_id=business_id,
            request_id="m7-request-123",
            metadata={
                "reason_code": "support_review",
                "state": "read_only",
            },
        )
        event_id = event.id

        await session.commit()

    async with session_factory() as session:
        stored = await session.get(AdminAuditEvent, event_id)

        assert stored is not None
        assert stored.actor_user_id == actor.id
        assert stored.actor_role == UserRole.SUPER_ADMIN
        assert stored.action == "user.inspect"
        assert stored.target_type == "user"
        assert stored.target_id == target_id
        assert stored.business_id == business_id
        assert stored.request_id == "m7-request-123"
        assert stored.metadata_json == {
            "reason_code": "support_review",
            "state": "read_only",
        }


@pytest.mark.integration
async def test_admin_audit_service_does_not_commit_callers_transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    actor = await create_operator(
        session_factory,
        email="m7-audit-rollback@example.com",
        role=UserRole.CUSTOMER_SERVICE,
    )

    async with session_factory() as session:
        event = await AdminAuditService(session).record(
            actor=actor,
            action="business.inspect",
            target_type="business",
            target_id=uuid4(),
            request_id="rollback-request",
            metadata={"reason_code": "support_lookup"},
        )
        event_id = event.id

        await session.rollback()

    async with session_factory() as session:
        stored = await session.get(AdminAuditEvent, event_id)
        assert stored is None


@pytest.mark.integration
async def test_admin_audit_rows_reject_database_update_and_delete(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    actor = await create_operator(
        session_factory,
        email="m7-audit-immutable@example.com",
        role=UserRole.SUPER_ADMIN,
    )

    async with session_factory() as session:
        event = await AdminAuditService(session).record(
            actor=actor,
            action="user.inspect",
            target_type="user",
            target_id=uuid4(),
            request_id="immutable-request",
            metadata={"reason_code": "immutability_test"},
        )
        event_id = event.id
        await session.commit()

    async with session_factory() as session:
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    """
                    UPDATE admin_audit_events
                    SET action = 'user.changed'
                    WHERE id = :event_id
                    """
                ),
                {"event_id": event_id},
            )
            await session.commit()

        await session.rollback()

    async with session_factory() as session:
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    """
                    DELETE FROM admin_audit_events
                    WHERE id = :event_id
                    """
                ),
                {"event_id": event_id},
            )
            await session.commit()

        await session.rollback()

    async with session_factory() as session:
        stored = (
            await session.execute(select(AdminAuditEvent).where(AdminAuditEvent.id == event_id))
        ).scalar_one()

        assert stored.action == "user.inspect"
