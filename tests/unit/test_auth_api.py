from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.api.deps import enforce_auth_rate_limit_dependency, get_auth_service
from app.core.session_cookie import REFRESH_COOKIE_NAME
from app.main import create_app
from app.schemas.auth import AccessTokenResponse, LoginRequest
from app.services.auth import SessionTokens, TokenValidationError


class FakeAuthService:
    def __init__(self) -> None:
        self.refreshed_with: str | None = None
        self.logged_out_with: str | None = None

    async def login(self, payload: LoginRequest) -> SessionTokens:
        return SessionTokens(
            access=AccessTokenResponse(
                access_token="access-one",
                access_token_expires_in=900,
            ),
            refresh_token="refresh-one",
            remember_me=payload.remember_me,
        )

    async def refresh(self, raw_token: str) -> SessionTokens:
        self.refreshed_with = raw_token
        return SessionTokens(
            access=AccessTokenResponse(
                access_token="access-two",
                access_token_expires_in=900,
            ),
            refresh_token="refresh-two",
            remember_me=True,
        )

    async def logout(self, raw_token: str) -> None:
        self.logged_out_with = raw_token


async def no_rate_limit() -> None:
    return None


async def test_browser_session_uses_rotating_httponly_refresh_cookie() -> None:
    app = create_app()
    service = FakeAuthService()
    app.dependency_overrides[get_auth_service] = lambda: service
    app.dependency_overrides[enforce_auth_rate_limit_dependency] = no_rate_limit

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "test@example.com",
                "password": "CorrectHorseBattery9",
                "remember_me": True,
            },
        )
        assert login.status_code == 200
        assert login.json() == {
            "access_token": "access-one",
            "token_type": "bearer",
            "access_token_expires_in": 900,
        }
        assert client.cookies.get(REFRESH_COOKIE_NAME) == "refresh-one"
        assert "httponly" in login.headers["set-cookie"].lower()
        assert "max-age=2592000" in login.headers["set-cookie"].lower()

        refresh = await client.post("/api/v1/auth/refresh")
        assert refresh.status_code == 200
        assert refresh.json()["access_token"] == "access-two"
        assert service.refreshed_with == "refresh-one"
        assert client.cookies.get(REFRESH_COOKIE_NAME) == "refresh-two"

        logout = await client.post("/api/v1/auth/logout")
        assert logout.status_code == 202
        assert service.logged_out_with == "refresh-two"
        assert client.cookies.get(REFRESH_COOKIE_NAME) is None


async def test_invalid_refresh_clears_cookie() -> None:
    app = create_app()
    service = FakeAuthService()

    async def reject_refresh(_raw_token: str) -> SessionTokens:
        raise TokenValidationError

    app.dependency_overrides[get_auth_service] = lambda: service
    app.dependency_overrides[enforce_auth_rate_limit_dependency] = no_rate_limit

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "test@example.com",
                "password": "CorrectHorseBattery9",
            },
        )
        assert login.status_code == 200
        assert "max-age" not in login.headers["set-cookie"].lower()
        service.refresh = reject_refresh  # type: ignore[method-assign]
        response = await client.post("/api/v1/auth/refresh")

        assert response.status_code == 401
        assert "max-age=0" in response.headers["set-cookie"].lower()
        assert client.cookies.get(REFRESH_COOKIE_NAME) is None


def test_auth_openapi_contract_keeps_refresh_token_out_of_json() -> None:
    schema = create_app().openapi()
    access_response = schema["components"]["schemas"]["AccessTokenResponse"]
    assert "refresh_token" not in access_response["properties"]
    assert "requestBody" not in schema["paths"]["/api/v1/auth/refresh"]["post"]
    assert "requestBody" not in schema["paths"]["/api/v1/auth/logout"]["post"]
    assert list(schema["components"]["schemas"]["RegisterRequest"]["properties"]) == [
        "full_name",
        "email",
        "password",
        "industry",
        "job_role",
    ]
