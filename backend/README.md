# Noblen AI Platform — Backend

FastAPI + SQLAlchemy 2.0 (async) + PostgreSQL. See the repository root
[`README.md`](../README.md) and [`ARCHITECTURE.md`](../ARCHITECTURE.md) for the
full picture.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# With PostgreSQL configured in ../.env:
alembic upgrade head
uvicorn app.main:app --reload
```

With the default (SQLite) `DATABASE_URL`, the app creates its schema at startup,
so you can boot with no database server for quick local exploration.

## Quality gate

```bash
ruff check .          # lint
ruff format --check . # formatting
mypy app              # type-check
pytest                # tests (SQLite-backed, no external services)
```

## Migrations

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
alembic downgrade -1
```

## Layout

```
app/
  core/      config, security, logging, exceptions, middleware, rate limiting
  db/        engine/session, declarative base + mixins, tenant scoping helper
  models/    ORM models (organizations, users, memberships, RBAC, teams, auth, audit)
  rbac/      permission catalogue + role → permission mappings
  schemas/   Pydantic request/response contracts
  services/  business logic (transaction boundaries, audit)
  api/       dependencies + versioned routers (/api/v1)
migrations/  Alembic environment + versions
tests/       health, auth, RBAC, tenant-isolation suites
```
