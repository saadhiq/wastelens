"""Aggregates all v1 routers under /api/v1."""

from fastapi import APIRouter

from app.api.v1 import (
    analytics,
    auth,
    bags,
    brands,
    captures,
    health,
    residents,
    review,
    vocabulary,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(residents.router)
api_router.include_router(bags.router)
api_router.include_router(captures.router)
api_router.include_router(analytics.router)
api_router.include_router(review.router)
api_router.include_router(vocabulary.router)
api_router.include_router(brands.router)
