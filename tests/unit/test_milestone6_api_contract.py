from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.main import create_app
from app.schemas.dashboard import DashboardUpdate


def test_dashboard_openapi_contract_is_tenant_derived() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]

    assert "/api/v1/dashboards" in paths
    assert "/api/v1/dashboards/{dashboard_id}" in paths

    collection = paths["/api/v1/dashboards"]
    resource = paths["/api/v1/dashboards/{dashboard_id}"]

    assert {"get", "post"} <= set(collection)
    assert {"get", "patch", "delete"} <= set(resource)

    create_properties = schema["components"]["schemas"]["DashboardCreate"]["properties"]
    assert set(create_properties) == {
        "dashboard_type",
        "title",
        "configuration",
    }
    assert "business_id" not in create_properties
    assert "created_by_user_id" not in create_properties


def test_dashboard_patch_rejects_null_and_empty_payloads() -> None:
    with pytest.raises(ValidationError, match="At least one dashboard field"):
        DashboardUpdate()

    with pytest.raises(ValidationError, match="Dashboard title cannot be null"):
        DashboardUpdate(title=None)

    with pytest.raises(
        ValidationError,
        match="Dashboard configuration cannot be null",
    ):
        DashboardUpdate(configuration=None)
