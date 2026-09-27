"""Aggregate v1 API router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    agents,
    ai,
    approvals,
    auth,
    conversations,
    knowledge,
    organizations,
    tools,
    users,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(organizations.router)
api_router.include_router(ai.router)
api_router.include_router(agents.router)
api_router.include_router(conversations.router)
api_router.include_router(tools.router)
api_router.include_router(approvals.router)
api_router.include_router(knowledge.router)
