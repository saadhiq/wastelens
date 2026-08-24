"""Aggregates all v1 routers under /api/v1."""

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    analytics,
    auth,
    bags,
    bins,
    brands,
    calendar,
    captures,
    collections,
    collectors,
    health,
    pickups,
    residents,
    review,
    stations,
    vocabulary,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(admin.router)
api_router.include_router(auth.router)
api_router.include_router(residents.router)
api_router.include_router(bags.router)
api_router.include_router(collections.router)
api_router.include_router(captures.router)
api_router.include_router(analytics.router)
api_router.include_router(review.router)
api_router.include_router(vocabulary.router)
api_router.include_router(brands.router)
api_router.include_router(pickups.router)
api_router.include_router(stations.router)
api_router.include_router(bins.router)
api_router.include_router(collectors.router)
api_router.include_router(calendar.router)
