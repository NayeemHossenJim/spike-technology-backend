from fastapi import APIRouter

from app.api.v1 import (
    ai,
    auth,
    billing,
    businesses,
    dashboards,
    health,
    plans,
    subscriptions,
    uploads,
    users,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(businesses.router)
api_router.include_router(plans.router)
api_router.include_router(subscriptions.subscription_router)
api_router.include_router(subscriptions.entitlement_router)
api_router.include_router(billing.router)
api_router.include_router(uploads.router)
api_router.include_router(dashboards.router)
api_router.include_router(ai.router)
api_router.include_router(health.router)
