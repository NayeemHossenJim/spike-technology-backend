from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.v1 import admin
from app.db.session import get_session
from app.main import create_app
from app.models.user import User, UserRole
from app.services.admin import AdminService, AdminUserPage


def operator(role: UserRole, *, email: str) -> User:
    return User(
        email=email,
        full_name="Platform Operator",
        password_hash="not-a-real-password-hash",
        role=role,
        is_active=True,
        is_verified=True,
    )


def customer_user() -> User:
    return User(
        email="customer@example.com",
        full_name="Customer User",
        password_hash="not-a-real-password-hash",
        role=UserRole.USER,
        is_active=True,
        is_verified=True,
    )


def test_m7_admin_routes_are_registered() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]

    assert "/api/v1/admin/users" in paths
    assert "/api/v1/admin/businesses" in paths
    assert "get" in paths["/api/v1/admin/users"]
    assert "get" in paths["/api/v1/admin/businesses"]


def test_customer_user_cannot_access_platform_admin_routes(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(admin.router)

    async def override_current_user() -> User:
        return customer_user()

    async def override_session():
        yield None

    async def fake_list_users(self, **kwargs):
        raise AssertionError("Admin service must not run for a customer user.")

    monkeypatch.setattr(
        AdminService,
        "list_users",
        fake_list_users,
    )

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_session] = override_session

    with TestClient(app) as client:
        response = client.get("/admin/users")

    assert response.status_code == 403


def test_customer_service_reads_customers_without_platform_staff(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(admin.router)

    support = operator(
        UserRole.CUSTOMER_SERVICE,
        email="support@example.com",
    )
    customer = customer_user()
    observed: dict[str, object] = {}

    async def override_current_user() -> User:
        return support

    async def override_session():
        yield None

    async def fake_list_users(
        self,
        *,
        query,
        limit,
        offset,
        include_platform_roles,
    ):
        observed["query"] = query
        observed["limit"] = limit
        observed["offset"] = offset
        observed["include_platform_roles"] = include_platform_roles

        return AdminUserPage(
            items=(customer,),
            total=1,
            limit=limit,
            offset=offset,
        )

    monkeypatch.setattr(
        AdminService,
        "list_users",
        fake_list_users,
    )

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_session] = override_session

    with TestClient(app) as client:
        response = client.get(
            "/admin/users",
            params={"q": "customer", "limit": 25},
        )

    assert response.status_code == 200
    assert observed["include_platform_roles"] is False

    payload = response.json()

    assert payload["total"] == 1
    assert payload["limit"] == 25
    assert payload["items"][0]["email"] == "customer@example.com"
    assert payload["items"][0]["role"] == "user"
    assert "password_hash" not in payload["items"][0]


def test_super_admin_may_enumerate_platform_roles(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(admin.router)

    super_admin = operator(
        UserRole.SUPER_ADMIN,
        email="admin@example.com",
    )
    observed: dict[str, object] = {}

    async def override_current_user() -> User:
        return super_admin

    async def override_session():
        yield None

    async def fake_list_users(
        self,
        *,
        query,
        limit,
        offset,
        include_platform_roles,
    ):
        observed["include_platform_roles"] = include_platform_roles

        return AdminUserPage(
            items=(super_admin,),
            total=1,
            limit=limit,
            offset=offset,
        )

    monkeypatch.setattr(
        AdminService,
        "list_users",
        fake_list_users,
    )

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_session] = override_session

    with TestClient(app) as client:
        response = client.get("/admin/users")

    assert response.status_code == 200
    assert observed["include_platform_roles"] is True

    payload = response.json()

    assert payload["items"][0]["role"] == "super_admin"
    assert "password_hash" not in payload["items"][0]
