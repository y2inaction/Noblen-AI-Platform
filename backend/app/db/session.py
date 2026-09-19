"""Async database engine and session management."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


def _create_engine(url: str) -> AsyncEngine:
    connect_args: dict = {}
    if url.startswith("sqlite"):
        # Needed for SQLite when shared across async tasks/threads in tests.
        connect_args["check_same_thread"] = False
    return create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        future=True,
        connect_args=connect_args,
    )


engine: AsyncEngine = _create_engine(settings.DATABASE_URL)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a database session with commit/rollback handling."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
