"""Fixtures for Knowledge/RAG tests — these run against a REAL PostgreSQL + pgvector.

The database URL comes from ``KNOWLEDGE_TEST_DATABASE_URL`` (default: local dev pg).
If PostgreSQL/pgvector is unavailable, the whole module is skipped so the rest of the
suite (SQLite) is unaffected. Mock embeddings are 1536-dim to match the vector column.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (populate metadata)
from app.agents.runtime import AgentRuntime, get_agent_runtime
from app.agents.tools.seed import seed_builtin_tools
from app.ai.gateway import AIGateway, get_ai_gateway
from app.ai.providers.mock import MockProvider
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app

PG_URL = os.environ.get(
    "KNOWLEDGE_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres@127.0.0.1:5432/noblen_test",
)
EMBED_DIM = settings.KNOWLEDGE_EMBEDDING_DIMENSION


def _mock_gateway() -> AIGateway:
    # Embeddings must match the fixed pgvector column dimension. The knowledge
    # config uses provider "openai" by default, so alias it to the mock here.
    mock = MockProvider(dimensions=EMBED_DIM)
    return AIGateway(
        providers={"mock": mock, "openai": mock},
        default_provider="mock",
        default_model="mock-1",
        default_embedding_provider="mock",
        default_embedding_model="mock-embed-1",
        max_retries=0,
    )


async def _pg_available() -> bool:
    try:
        engine = create_async_engine(PG_URL)
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await engine.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest_asyncio.fixture
async def pg_engine():
    if not await _pg_available():
        pytest.skip("PostgreSQL + pgvector not available for Knowledge/RAG tests")
    engine = create_async_engine(PG_URL)
    # Reset via schema drop for a clean slate regardless of prior state (the
    # circular use_alter FK on `agents` makes metadata.drop_all fragile across
    # schema versions).
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_sessions(pg_engine):
    return async_sessionmaker(bind=pg_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def pg_client(pg_sessions) -> AsyncGenerator[AsyncClient, None]:
    # Point storage at a temp dir so uploads don't touch the repo.
    tmp = tempfile.mkdtemp(prefix="noblen-kb-")
    old_storage = settings.KNOWLEDGE_STORAGE_DIR
    settings.KNOWLEDGE_STORAGE_DIR = tmp

    async with pg_sessions() as session:
        await seed_builtin_tools(session)
        await session.commit()

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with pg_sessions() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    gateway = _mock_gateway()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_ai_gateway] = lambda: gateway
    app.dependency_overrides[get_agent_runtime] = lambda: AgentRuntime(gateway=gateway)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    settings.KNOWLEDGE_STORAGE_DIR = old_storage
