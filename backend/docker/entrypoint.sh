#!/usr/bin/env bash
# Backend container entrypoint: run DB migrations, then exec the given command.
set -euo pipefail

echo "[entrypoint] Applying database migrations..."
# Only run Alembic against a real (non-SQLite) database.
if [[ "${SKIP_MIGRATIONS:-false}" == "true" ]]; then
  # e.g. background workers: the API container owns migrations.
  echo "[entrypoint] SKIP_MIGRATIONS=true; not running Alembic."
elif [[ "${DATABASE_URL:-}" == postgresql* ]]; then
  alembic upgrade head
else
  echo "[entrypoint] Non-PostgreSQL DATABASE_URL detected; skipping Alembic (dev mode)."
fi

echo "[entrypoint] Starting: $*"
exec "$@"
