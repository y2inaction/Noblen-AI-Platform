"""PostgreSQL application-role test harness (Milestone 10, M10.2).

Enabled by ``TEST_DATABASE_URL``, an administrative (superuser) connection URL.
Without it the suite runs on SQLite exactly as before.

- **Once per session:** create the non-owner application role (`noblen_app`) if it is
  missing, and build a template database migrated by Alembic as the administrator.
  Then grant the role DML only.
- **Per test:** clone a fresh database from that template (`CREATE DATABASE ...
  TEMPLATE`), so every test starts from the real migrated schema. There is no shared
  cleanup between tests.
- **Tests and the app connect as `noblen_app`.** It is not a superuser, does not own
  the tables, cannot create databases or roles, and has no `BYPASSRLS`: the
  application's security boundary (ADR-0034).

The administrator is used only to create the role and databases, and to run
migrations, never for application behavior.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

ADMIN_URL_ENV = "TEST_DATABASE_URL"
APP_ROLE = os.environ.get("TEST_APP_ROLE", "noblen_app")
# Test-only credential for a role that exists only in test clusters.
APP_PASSWORD = os.environ.get("TEST_APP_ROLE_PASSWORD", "noblen_app_test")
BACKEND_DIR = Path(__file__).resolve().parents[1]


def enabled() -> bool:
    return bool(os.environ.get(ADMIN_URL_ENV))


def _admin_url() -> URL:
    return make_url(os.environ[ADMIN_URL_ENV])


class Harness:
    """Session state: the template database and the application role."""

    def __init__(self) -> None:
        self.admin = _admin_url()
        self.template = f"noblen_tpl_{uuid.uuid4().hex[:10]}"

    # -- administration (autocommit: CREATE/DROP DATABASE cannot run in a transaction)
    async def _aexecute(self, *statements: str, database: str | None = None) -> None:
        url = self.admin.set(database=database or self.admin.database)
        engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                for statement in statements:
                    await conn.execute(text(statement))
        finally:
            await engine.dispose()

    def _execute(self, *statements: str, database: str | None = None) -> None:
        """Session setup and teardown run outside the test event loop."""
        asyncio.run(self._aexecute(*statements, database=database))

    def setup(self) -> None:
        self._execute(
            f"""
            DO $$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'
                  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION;
              END IF;
            END $$;
            """,
            f"CREATE DATABASE {self.template}",
        )
        self._execute("CREATE EXTENSION IF NOT EXISTS vector", database=self.template)
        env = {
            **os.environ,
            "DATABASE_URL": self.admin.set(database=self.template).render_as_string(
                hide_password=False
            ),
        }
        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
        )
        if migrated.returncode != 0:
            raise RuntimeError(f"alembic upgrade failed:\n{migrated.stdout}\n{migrated.stderr}")
        # DML only. The administrator owns every table; the app role owns nothing.
        self._execute(
            f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
            database=self.template,
        )

    async def new_database(self) -> str:
        name = f"noblen_t_{uuid.uuid4().hex[:12]}"
        await self._aexecute(f"CREATE DATABASE {name} TEMPLATE {self.template}")
        return name

    async def drop_database(self, name: str) -> None:
        await self._aexecute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")

    def app_url(self, database: str) -> URL:
        """The application's connection: `noblen_app` on a test database."""
        return self.admin.set(username=APP_ROLE, password=APP_PASSWORD, database=database)

    def teardown(self) -> None:
        self._execute(f"DROP DATABASE IF EXISTS {self.template} WITH (FORCE)")


ROLE_CHECK = """
SELECT current_user, r.rolsuper, r.rolbypassrls,
       (SELECT count(*) FROM pg_tables
         WHERE schemaname = 'public' AND tableowner = current_user) AS owned_tables
FROM pg_roles r WHERE r.rolname = current_user
"""


def assert_application_role(row: dict) -> None:
    """The connection used by the tests must be the non-owner application role."""
    assert row["current_user"] == APP_ROLE, row
    assert row["rolsuper"] is False, "the application role must not be a superuser"
    assert row["rolbypassrls"] is False, "the application role must not bypass RLS"
    assert row["owned_tables"] == 0, "the application role must not own tables"


_role_checked = False


@asynccontextmanager
async def fresh_app_engine(harness: Harness) -> AsyncIterator[AsyncEngine]:
    """A fresh migrated database for one test, connected as the application role.
    The role is checked once per session."""
    global _role_checked
    name = await harness.new_database()
    engine = create_async_engine(harness.app_url(name))
    try:
        if not _role_checked:
            async with engine.connect() as conn:
                row = (await conn.execute(text(ROLE_CHECK))).mappings().one()
            assert_application_role(dict(row))
            _role_checked = True
        yield engine
    finally:
        await engine.dispose()
        await harness.drop_database(name)
