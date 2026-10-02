# Development

## Prerequisites
- Python 3.11, Node 22, Docker + Docker Compose (optional but recommended).

## Environment
```bash
cp .env.example .env
openssl rand -hex 32   # paste into JWT_SECRET
```

## Full stack via Docker
```bash
docker compose up --build
# Frontend  http://localhost:3000
# API       http://localhost:8000  (docs at /docs)
# Nginx     http://localhost:8080
```

## Backend (local)
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head        # against a real Postgres; SQLite auto-creates on boot
uvicorn app.main:app --reload
```

### Backend quality gate
```bash
ruff check .          # lint
ruff format --check . # formatting
mypy app              # type-check
pytest                # tests (SQLite-backed)
```

### Tests on PostgreSQL, as the application role (Milestone 10)
The same suite runs on PostgreSQL + pgvector when `TEST_DATABASE_URL` points at an
administrative (superuser) connection:
```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres pytest
```
- **Once per session:** the harness (`tests/pg_harness.py`) creates the non-owner
  role `noblen_app` if it is missing, then builds a template database migrated by
  Alembic.
- **Per test:** it clones a fresh database from that template.
- **Connections:** tests and the app connect as `noblen_app`, which is not a
  superuser, owns no tables, cannot create databases or roles, and has no
  `BYPASSRLS`. The superuser only creates the role and databases and runs
  migrations.
- **CI:** the job "Backend tests (PostgreSQL, application role)" runs the suite this
  way.
- **Overrides:** `TEST_APP_ROLE` and `TEST_APP_ROLE_PASSWORD` (test-only) change the
  role's name and password.

## Frontend (local)
```bash
cd frontend
npm install
npm run dev            # http://localhost:3000
npm run lint
npm run typecheck
npm run build
```

## UI smoke test
Drives the operating environment in a real browser. It needs no API keys: the
backend uses the built-in mock model.
```bash
# 1. backend (fresh SQLite DB) and worker, with the mock model
cd backend
export DATABASE_URL=sqlite+aiosqlite:///./smoke.db AI_DEFAULT_PROVIDER=mock AI_DEFAULT_MODEL=mock-1 RATE_LIMIT_ENABLED=false
uvicorn app.main:app --port 8000 &
python -m app.agents.worker &
# 2. frontend
cd ../frontend && npm run build && npm start &
# 3. run (screenshots go to ./e2e-shots)
npm i --no-save playwright-core && node e2e/smoke.mjs e2e-shots
```
Set `CHROMIUM_PATH` if Playwright's own browser is not installed.

## Conventions
- Conventional Commits: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`.
- Keep routers thin; put business logic in services; scope every tenant query.
- Add/keep tests green — especially `tests/test_tenant_isolation.py`.
- Update `CHANGELOG.md`, `PROJECT_ROADMAP.md`, and relevant `docs/` at the end of
  each phase (the Quality Gate, spec §46).
