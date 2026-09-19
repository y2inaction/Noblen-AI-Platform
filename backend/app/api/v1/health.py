"""Liveness and readiness endpoints (ARCHITECTURE.md §12)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.core.config import settings
from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict:
    return {
        "status": "ok",
        "service": settings.PROJECT_NAME,
        "version": __version__,
        "environment": settings.ENVIRONMENT,
    }


@router.get("/readiness", summary="Readiness probe (checks dependencies)")
async def readiness(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    checks: dict[str, str] = {}
    ready = True
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {type(exc).__name__}"
        ready = False

    status_code = 200 if ready else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )
