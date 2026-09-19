#!/usr/bin/env bash
# Backend container entrypoint: run DB migrations, then exec the given command.
set -euo pipefail

echo "[entrypoint] Applying database migrations..."
# Only run Alembic against a real (non-SQLite) database.
if [[ "${DATABASE_URL:-}" == postgresql* ]]; then
  alembic upgrade head
else
  echo "[entrypoint] Non-PostgreSQL DATABASE_URL detected; skipping Alembic (dev mode)."
fi

echo "[entrypoint] Starting: $*"
exec "$@"
