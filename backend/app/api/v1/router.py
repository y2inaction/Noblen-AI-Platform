"""Aggregate v1 API router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import ai, auth, organizations, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(organizations.router)
api_router.include_router(ai.router)
