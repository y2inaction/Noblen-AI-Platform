"""Pytest fixtures: an isolated database per test and an httpx client wired to the
FastAPI app. The database is in-memory SQLite by default. With TEST_DATABASE_URL set,
it is a fresh PostgreSQL database per test, used as the non-owner application role
(`tests/pg_harness.py`, Milestone 10).

Tests never touch external services (see DECISIONS.md ADR-0006).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

# Configure a deterministic, isolated environment BEFORE importing the app.
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-0123456789abcdef")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.models  # noqa: E402,F401  (populate metadata)
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from tests import pg_harness  # noqa: E402


@pytest.fixture(scope="session")
def pg():
    """The PostgreSQL application-role harness when TEST_DATABASE_URL is set (M10.2)."""
    if not pg_harness.enabled():
        yield None
        return
    harness = pg_harness.Harness()
    harness.setup()
    yield harness
    harness.teardown()


@pytest_asyncio.fixture
async def db_engine(pg):
    if pg is not None:
        # A fresh database cloned from the migrated template, used as `noblen_app`.
        async with pg_harness.fresh_app_engine(pg) as engine:
            yield engine
        return
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(session_factory) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(session_factory) -> AsyncGenerator[AsyncClient, None]:
    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def register_org(
    client: AsyncClient, email: str, org_name: str, password: str = "Password123!"
):
    """Helper: register a user+org and return the parsed AuthResponse dict."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "organization_name": org_name,
            "full_name": email.split("@")[0],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def auth_headers(auth: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth['tokens']['access_token']}"}


async def create_user(db: AsyncSession) -> uuid.UUID:
    """A real user row, for service-level tests that need a referenced user
    (PostgreSQL enforces the foreign keys that SQLite does not)."""
    from app.models.user import User

    user = User(email=f"u-{uuid.uuid4().hex[:12]}@example.com", hashed_password="x")
    db.add(user)
    await db.flush()
    return user.id


async def create_org(db: AsyncSession) -> uuid.UUID:
    """A real organization row, for the same reason as `create_user`."""
    from app.models.organization import Organization

    org = Organization(name="Acme", slug=f"acme-{uuid.uuid4().hex[:8]}")
    db.add(org)
    await db.flush()
    return org.id
