# Changelog

All notable changes to the Noblen AI Platform are documented here.
The format is loosely based on [Keep a Changelog](https://keepachangelog.com/),
and the project uses Conventional Commits.

## [Unreleased]

### Added — Phase 0 (Discovery)
- Repository discovery and runtime inventory.
- `ARCHITECTURE.md`, `PROJECT_ROADMAP.md`, `DECISIONS.md`, `README.md`, `.gitignore`,
  `.env.example`, and the `docs/` documentation set.

### Added — Phase 1 (Foundation)
- FastAPI backend skeleton: env-driven config, structured JSON logging with request
  IDs, typed exceptions, CORS, secure headers, and basic per-IP rate limiting.
- Health (`/health`) and readiness (`/readiness`) endpoints.
- Async SQLAlchemy 2.0 database layer + Alembic migrations (initial revision).
- Core multi-tenant models: organizations, users, organization members, roles,
  permissions, teams, refresh tokens, email-verification & password-reset tokens,
  audit logs.
- Authentication: register (creates organization + owner), login, rotating refresh,
  logout, current-user, and email-verification / password-reset scaffolding.
  Argon2id password hashing.
- Server-side RBAC (`SUPER_ADMIN`/`ADMIN`/`MANAGER`/`MEMBER`/`VIEWER`) and
  tenant-scoping utilities.
- Next.js + TypeScript + Tailwind frontend scaffold (login + dashboard shell).
- Test suites: health, auth, RBAC, and tenant isolation.
- Docker (backend + frontend), dev/prod Docker Compose, Nginx reverse proxy,
  and a GitHub Actions CI pipeline (lint, type-check, test, build, docker build).
