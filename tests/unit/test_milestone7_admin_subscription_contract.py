from __future__ import annotations

from app.main import create_app
from app.schemas.admin import AdminSubscriptionRead


def test_stage4_admin_subscription_route_is_read_only() -> None:
    paths = create_app().openapi()["paths"]
    path = paths["/api/v1/admin/businesses/{business_id}/subscription"]

    assert set(path) == {"get"}


def test_admin_subscription_schema_excludes_sensitive_stripe_fields() -> None:
    fields = set(AdminSubscriptionRead.model_fields)

    assert {
        "status",
        "plan",
        "stripe_managed",
        "last_stripe_synced_at",
        "access",
        "entitlements",
    }.issubset(fields)

    assert fields.isdisjoint(
        {
            "stripe_customer_id",
            "stripe_subscription_id",
            "stripe_price_id",
            "last_stripe_event_id",
            "last_stripe_event_created_at",
            "hosted_invoice_url",
            "invoice_pdf_url",
            "payment_method",
            "card",
        }
    )


def test_stage4_does_not_create_admin_subscription_mutation_routes() -> None:
    paths = create_app().openapi()["paths"]

    forbidden = {
        "/api/v1/admin/businesses/{business_id}/subscription/cancel",
        "/api/v1/admin/businesses/{business_id}/subscription/resume",
        "/api/v1/admin/businesses/{business_id}/subscription/override",
        "/api/v1/admin/businesses/{business_id}/subscription/plan",
    }

    assert forbidden.isdisjoint(paths)
