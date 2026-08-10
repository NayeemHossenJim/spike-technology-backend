from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.core.session_cookie import REFRESH_COOKIE_NAME
from app.models.admin import AdminAuditEvent
from app.models.auth import RefreshToken
from app.models.user import User, UserRole
from app.schemas.admin import AdminAccountActionReason
from app.services.admin_accounts import AdminAccountService
from app.services.admin_audit import AdminAuditService
from tests.conftest import InMemoryEmailSender

PASSWORD = "CorrectHorseBattery9"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def register_and_verify(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    *,
    email: str,
    full_name: str,
) -> UUID:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": full_name,
            "email": email,
            "password": PASSWORD,
            "industry": "Technology",
            "job_role": "Engineer / Developer",
        },
    )
    assert registration.status_code == 201

    verification = await client.post(
        "/api/v1/auth/verify-email",
        json={
            "email": email,
            "otp": email_sender.verification_otps[-1][1],
        },
    )
    assert verification.status_code == 200

    return UUID(registration.json()["id"])


async def login(
    client: AsyncClient,
    *,
    email: str,
    remember_me: bool = False,
) -> tuple[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": PASSWORD,
            "remember_me": remember_me,
        },
    )
    assert response.status_code == 200

    refresh_token = client.cookies.get(REFRESH_COOKIE_NAME)
    assert refresh_token

    return response.json()["access_token"], refresh_token


async def create_customer(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    *,
    email: str,
    full_name: str,
    business_name: str,
) -> tuple[UUID, str, str, UUID]:
    user_id = await register_and_verify(
        client,
        email_sender,
        email=email,
        full_name=full_name,
    )

    access_token, refresh_token = await login(
        client,
        email=email,
        remember_me=True,
    )

    onboarding = await client.post(
        "/api/v1/businesses",
        headers=bearer(access_token),
        json={"name": business_name},
    )
    assert onboarding.status_code == 201

    return (
        user_id,
        access_token,
        refresh_token,
        UUID(onboarding.json()["business"]["id"]),
    )


async def create_platform_operator(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    email: str,
    full_name: str,
    role: UserRole,
) -> tuple[UUID, str]:
    user_id = await register_and_verify(
        client,
        email_sender,
        email=email,
        full_name=full_name,
    )

    async with session_factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.role = role
        await session.commit()

    access_token, _ = await login(
        client,
        email=email,
    )

    return user_id, access_token


@pytest.mark.integration
async def test_super_admin_suspend_reactivate_invalidates_old_sessions_forever(
    client: AsyncClient,
    app,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    (
        target_id,
        old_access_token,
        old_refresh_token,
        business_id,
    ) = await create_customer(
        client,
        email_sender,
        email="m7-lifecycle-customer@example.com",
        full_name="Lifecycle Customer",
        business_name="Lifecycle Analytics",
    )

    admin_id, admin_access_token = await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-lifecycle-admin@example.com",
        full_name="Lifecycle Super Admin",
        role=UserRole.SUPER_ADMIN,
    )

    suspend = await client.post(
        f"/api/v1/admin/users/{target_id}/suspend",
        headers={
            **bearer(admin_access_token),
            "X-Request-ID": "m7-suspend-request",
        },
        json={"reason_code": "security_review"},
    )

    assert suspend.status_code == 200

    suspended = suspend.json()
    assert suspended["changed"] is True
    assert suspended["sessions_revoked"] == 1
    assert suspended["user"]["id"] == str(target_id)
    assert suspended["user"]["is_active"] is False

    old_access_while_suspended = await client.get(
        "/api/v1/users/me",
        headers=bearer(old_access_token),
    )
    assert old_access_while_suspended.status_code == 401

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as replay_client:
        old_refresh_while_suspended = await replay_client.post(
            "/api/v1/auth/refresh",
            headers={"Cookie": (f"{REFRESH_COOKIE_NAME}={old_refresh_token}")},
        )

    assert old_refresh_while_suspended.status_code == 401

    async with session_factory() as session:
        target = await session.get(User, target_id)
        assert target is not None
        assert target.is_active is False
        assert target.auth_session_version == 1

        refresh_tokens = (
            (await session.execute(select(RefreshToken).where(RefreshToken.user_id == target_id)))
            .scalars()
            .all()
        )

        assert len(refresh_tokens) == 1
        assert refresh_tokens[0].revoked_at is not None

        suspend_event = (
            await session.execute(
                select(AdminAuditEvent).where(
                    AdminAuditEvent.target_id == target_id,
                    AdminAuditEvent.action == "user.suspend",
                )
            )
        ).scalar_one()

        assert suspend_event.actor_user_id == admin_id
        assert suspend_event.actor_role == UserRole.SUPER_ADMIN
        assert suspend_event.business_id == business_id
        assert suspend_event.request_id == "m7-suspend-request"
        assert suspend_event.metadata_json == {
            "reason_code": "security_review",
            "previous_is_active": True,
            "current_is_active": False,
            "changed": True,
            "sessions_revoked": 1,
        }

    reactivate = await client.post(
        f"/api/v1/admin/users/{target_id}/reactivate",
        headers={
            **bearer(admin_access_token),
            "X-Request-ID": "m7-reactivate-request",
        },
        json={"reason_code": "support_resolution"},
    )

    assert reactivate.status_code == 200

    reactivated = reactivate.json()
    assert reactivated["changed"] is True
    assert reactivated["sessions_revoked"] == 0
    assert reactivated["user"]["is_active"] is True

    # Crucial generation test: the exact access JWT issued before suspension
    # must never become valid again merely because is_active became true.
    old_access_after_reactivation = await client.get(
        "/api/v1/users/me",
        headers=bearer(old_access_token),
    )
    assert old_access_after_reactivation.status_code == 401

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as replay_client:
        old_refresh_after_reactivation = await replay_client.post(
            "/api/v1/auth/refresh",
            headers={"Cookie": (f"{REFRESH_COOKIE_NAME}={old_refresh_token}")},
        )

    assert old_refresh_after_reactivation.status_code == 401

    new_access_token, _ = await login(
        client,
        email="m7-lifecycle-customer@example.com",
        remember_me=True,
    )

    new_session = await client.get(
        "/api/v1/users/me",
        headers=bearer(new_access_token),
    )
    assert new_session.status_code == 200

    async with session_factory() as session:
        target = await session.get(User, target_id)
        assert target is not None
        assert target.is_active is True
        assert target.auth_session_version == 1

        refresh_tokens = (
            (await session.execute(select(RefreshToken).where(RefreshToken.user_id == target_id)))
            .scalars()
            .all()
        )

        assert len(refresh_tokens) == 2
        assert sum(token.revoked_at is not None for token in refresh_tokens) == 1
        assert sum(token.revoked_at is None for token in refresh_tokens) == 1

        reactivate_event = (
            await session.execute(
                select(AdminAuditEvent).where(
                    AdminAuditEvent.target_id == target_id,
                    AdminAuditEvent.action == "user.reactivate",
                )
            )
        ).scalar_one()

        assert reactivate_event.actor_user_id == admin_id
        assert reactivate_event.business_id == business_id
        assert reactivate_event.request_id == "m7-reactivate-request"
        assert reactivate_event.metadata_json == {
            "reason_code": "support_resolution",
            "previous_is_active": False,
            "current_is_active": True,
            "changed": True,
            "sessions_revoked": 0,
        }


@pytest.mark.integration
async def test_account_lifecycle_permission_matrix_and_target_protection(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    (
        customer_id,
        customer_access_token,
        _,
        _,
    ) = await create_customer(
        client,
        email_sender,
        email="m7-permission-customer@example.com",
        full_name="Permission Customer",
        business_name="Permission Business",
    )

    support_id, support_access_token = await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-permission-support@example.com",
        full_name="Permission Support",
        role=UserRole.CUSTOMER_SERVICE,
    )

    admin_id, admin_access_token = await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-permission-admin@example.com",
        full_name="Permission Admin",
        role=UserRole.SUPER_ADMIN,
    )

    customer_attempt = await client.post(
        f"/api/v1/admin/users/{customer_id}/suspend",
        headers=bearer(customer_access_token),
        json={"reason_code": "security_review"},
    )
    assert customer_attempt.status_code == 403

    support_attempt = await client.post(
        f"/api/v1/admin/users/{customer_id}/suspend",
        headers=bearer(support_access_token),
        json={"reason_code": "security_review"},
    )
    assert support_attempt.status_code == 403

    platform_target = await client.post(
        f"/api/v1/admin/users/{support_id}/suspend",
        headers=bearer(admin_access_token),
        json={"reason_code": "security_review"},
    )
    assert platform_target.status_code == 403

    self_target = await client.post(
        f"/api/v1/admin/users/{admin_id}/suspend",
        headers=bearer(admin_access_token),
        json={"reason_code": "security_review"},
    )
    assert self_target.status_code == 403

    unknown_target = await client.post(
        f"/api/v1/admin/users/{uuid4()}/suspend",
        headers=bearer(admin_access_token),
        json={"reason_code": "security_review"},
    )
    assert unknown_target.status_code == 404

    async with session_factory() as session:
        customer = await session.get(User, customer_id)
        assert customer is not None
        assert customer.is_active is True
        assert customer.auth_session_version == 0

        events = (await session.execute(select(AdminAuditEvent))).scalars().all()

        assert events == []


@pytest.mark.integration
async def test_account_lifecycle_repeated_actions_are_deterministic(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target_id, _, _, _ = await create_customer(
        client,
        email_sender,
        email="m7-idempotent-customer@example.com",
        full_name="Idempotent Customer",
        business_name="Idempotent Business",
    )

    _, admin_access_token = await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-idempotent-admin@example.com",
        full_name="Idempotent Admin",
        role=UserRole.SUPER_ADMIN,
    )

    first_suspend = await client.post(
        f"/api/v1/admin/users/{target_id}/suspend",
        headers=bearer(admin_access_token),
        json={"reason_code": "administrative_hold"},
    )
    second_suspend = await client.post(
        f"/api/v1/admin/users/{target_id}/suspend",
        headers=bearer(admin_access_token),
        json={"reason_code": "administrative_hold"},
    )

    assert first_suspend.status_code == 200
    assert second_suspend.status_code == 200

    assert first_suspend.json()["changed"] is True
    assert first_suspend.json()["sessions_revoked"] == 1
    assert second_suspend.json()["changed"] is False
    assert second_suspend.json()["sessions_revoked"] == 0

    async with session_factory() as session:
        target = await session.get(User, target_id)
        assert target is not None
        assert target.is_active is False
        assert target.auth_session_version == 1

    first_reactivate = await client.post(
        f"/api/v1/admin/users/{target_id}/reactivate",
        headers=bearer(admin_access_token),
        json={"reason_code": "support_resolution"},
    )
    second_reactivate = await client.post(
        f"/api/v1/admin/users/{target_id}/reactivate",
        headers=bearer(admin_access_token),
        json={"reason_code": "support_resolution"},
    )

    assert first_reactivate.status_code == 200
    assert second_reactivate.status_code == 200

    assert first_reactivate.json()["changed"] is True
    assert second_reactivate.json()["changed"] is False

    async with session_factory() as session:
        target = await session.get(User, target_id)
        assert target is not None
        assert target.is_active is True

        # Repeated suspend/reactivate must never increase generation twice.
        assert target.auth_session_version == 1

        events = (
            (
                await session.execute(
                    select(AdminAuditEvent)
                    .where(AdminAuditEvent.target_id == target_id)
                    .order_by(
                        AdminAuditEvent.created_at,
                        AdminAuditEvent.id,
                    )
                )
            )
            .scalars()
            .all()
        )

        assert len(events) == 4

        observed = {
            (
                event.action,
                event.metadata_json["changed"],
            )
            for event in events
        }

        assert observed == {
            ("user.suspend", True),
            ("user.suspend", False),
            ("user.reactivate", True),
            ("user.reactivate", False),
        }

        assert sum(event.action == "user.suspend" for event in events) == 2

        assert sum(event.action == "user.reactivate" for event in events) == 2


@pytest.mark.integration
async def test_audit_failure_rolls_back_entire_suspension_transaction(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_id, _, _, _ = await create_customer(
        client,
        email_sender,
        email="m7-rollback-customer@example.com",
        full_name="Rollback Customer",
        business_name="Rollback Business",
    )

    admin_id, _ = await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-rollback-admin@example.com",
        full_name="Rollback Admin",
        role=UserRole.SUPER_ADMIN,
    )

    async def fail_audit(
        _self: AdminAuditService,
        **_kwargs: object,
    ) -> None:
        raise RuntimeError("forced audit failure")

    monkeypatch.setattr(
        AdminAuditService,
        "record",
        fail_audit,
    )

    async with session_factory() as session:
        actor = await session.get(User, admin_id)
        assert actor is not None

        with pytest.raises(
            RuntimeError,
            match="forced audit failure",
        ):
            await AdminAccountService(session).suspend_user(
                actor=actor,
                target_user_id=target_id,
                reason_code=(AdminAccountActionReason.SECURITY_REVIEW),
                request_id="m7-rollback-request",
            )

    async with session_factory() as session:
        target = await session.get(User, target_id)
        assert target is not None

        assert target.is_active is True
        assert target.auth_session_version == 0

        refresh_tokens = (
            (await session.execute(select(RefreshToken).where(RefreshToken.user_id == target_id)))
            .scalars()
            .all()
        )

        assert len(refresh_tokens) == 1
        assert refresh_tokens[0].revoked_at is None

        audit_events = (
            (
                await session.execute(
                    select(AdminAuditEvent).where(AdminAuditEvent.target_id == target_id)
                )
            )
            .scalars()
            .all()
        )

        assert audit_events == []
