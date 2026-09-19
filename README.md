# Noblen AI Platform

> Modular, multi-tenant AI business-automation platform by
> **[Noblen AI Solutions](https://www.noblenai.com)** (part of Noblen Technologies
> Limited), built for Nigerian and African businesses, professionals, NGOs,
> institutions and enterprises — and international clients.

Noblen AI Platform is **one reusable platform**, not a bundle of separate apps. All
commercial services (AI Customer Service, Sales, Content, Executive Assistant,
Operations, Custom Agents, Knowledge Intelligence, Training) run on a single shared
core: authentication, organizations, RBAC, an AI gateway, an agent engine, memory, a
knowledge base, a workflow engine, integrations, billing, usage metering, and audit
logging.

## Status

🚧 **Early build.** Phase 0 (Discovery) is complete and Phase 1 (Foundation) is in
progress. See [`PROJECT_ROADMAP.md`](./PROJECT_ROADMAP.md) for the phase plan and
[`ARCHITECTURE.md`](./ARCHITECTURE.md) for the design.

## Tech stack

- **Frontend:** Next.js (App Router) · TypeScript · Tailwind CSS
- **Backend:** Python 3.11 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 (async) · Alembic
- **Database:** PostgreSQL 16 (+ pgvector) · **Cache/queue:** Redis · Celery
- **AI:** provider abstraction → Anthropic, OpenAI · **Payments:** Paystack
- **Infra:** Docker · Docker Compose · Nginx · Ubuntu / AWS EC2

## Repository layout

```
backend/    FastAPI app (core, models, schemas, services, api, migrations, tests)
frontend/   Next.js dashboard
infra/      Nginx config + deployment/backup scripts
docs/       architecture, database, api, agents, workflows, security, deployment,
            integrations, development
```

## Quick start (development)

Prerequisites: Docker + Docker Compose (or local Python 3.11 & Node 22).

```bash
# 1. Configure environment (never commit .env)
cp .env.example .env
# generate a strong secret and paste it into JWT_SECRET
openssl rand -hex 32

# 2. Bring up the stack (Postgres, Redis, backend, frontend, Nginx)
docker compose up --build
```

- API:            http://localhost:8000
- API docs:       http://localhost:8000/docs
- Frontend:       http://localhost:3000
- Via Nginx:      http://localhost:8080  (frontend + `/api` proxied to the backend)

### Running the backend locally (without Docker)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
# point DATABASE_URL at a local Postgres, then:
alembic upgrade head
uvicorn app.main:app --reload
```

### Backend quality gate

```bash
cd backend
ruff check .          # lint
ruff format --check . # format
mypy app              # type-check
pytest                # tests (SQLite-backed, no external services needed)
```

### Frontend

```bash
cd frontend
npm install
npm run dev      # http://localhost:3000
npm run lint
npm run build
```

## Security

Secrets are provided only via environment variables — none are committed. Passwords
are hashed with Argon2id, permissions are enforced server-side, and every tenant-owned
record is scoped by `organization_id`. See [`docs/security.md`](./docs/security.md).

## Documentation

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — system design
- [`PROJECT_ROADMAP.md`](./PROJECT_ROADMAP.md) — phased delivery plan
- [`DECISIONS.md`](./DECISIONS.md) — architecture decision records
- [`docs/`](./docs) — deep-dive docs per subsystem
- [`CHANGELOG.md`](./CHANGELOG.md)

## License

Proprietary — © Noblen Technologies Limited. All rights reserved.
