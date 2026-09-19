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

## Frontend (local)
```bash
cd frontend
npm install
npm run dev            # http://localhost:3000
npm run lint
npm run typecheck
npm run build
```

## Conventions
- Conventional Commits: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`.
- Keep routers thin; put business logic in services; scope every tenant query.
- Add/keep tests green — especially `tests/test_tenant_isolation.py`.
- Update `CHANGELOG.md`, `PROJECT_ROADMAP.md`, and relevant `docs/` at the end of
  each phase (the Quality Gate, spec §46).
