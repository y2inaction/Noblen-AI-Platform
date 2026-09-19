"""Noblen AI Platform — FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.v1.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecureHeadersMiddleware,
)
from app.db.base import Base
from app.db.session import engine

configure_logging(debug=settings.DEBUG)
logger = get_logger("app")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Dev convenience: on SQLite, create tables at startup so the app runs with
    # no migration step. PostgreSQL always uses Alembic migrations.
    if settings.DATABASE_URL.startswith("sqlite") and not settings.is_production:
        import app.models  # noqa: F401  (populate metadata)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("startup_sqlite_schema_created")
    logger.info("startup", environment=settings.ENVIRONMENT, version=__version__)
    yield
    await engine.dispose()
    logger.info("shutdown")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=__version__,
    description="Multi-tenant AI business-automation platform by Noblen AI Solutions.",
    lifespan=lifespan,
)

# ---- Middleware (order matters: last added runs first) ----
app.add_middleware(RateLimitMiddleware, enabled=settings.RATE_LIMIT_ENABLED)
app.add_middleware(SecureHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Exception handlers (never leak internals; always structured) ----
@app.exception_handler(AppError)
async def _app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def _validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed.",
                "details": exc.errors(),
            }
        },
    )


@app.exception_handler(Exception)
async def _unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", error_type=type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "An unexpected error occurred."}},
    )


# ---- Routes ----
app.include_router(health_router)
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/", tags=["root"])
async def root() -> dict:
    return {
        "name": settings.PROJECT_NAME,
        "version": __version__,
        "docs": "/docs",
        "api": settings.API_V1_PREFIX,
    }
