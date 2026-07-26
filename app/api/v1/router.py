from fastapi import APIRouter

from app.api.v1 import auth, businesses, health, plans, subscriptions, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(businesses.router)
api_router.include_router(plans.router)
api_router.include_router(subscriptions.subscription_router)
api_router.include_router(subscriptions.entitlement_router)
api_router.include_router(health.router)
