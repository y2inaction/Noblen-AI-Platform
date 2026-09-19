#!/usr/bin/env bash
# Noblen AI Platform — PostgreSQL backup helper.
#
# Dumps the Noblen database from the isolated compose project to a timestamped,
# compressed file. Does not touch other databases or containers.
#
# Usage:
#   ./infra/scripts/backup.sh [output_dir]
set -euo pipefail

PROJECT_NAME="noblen-ai-platform"
OUT_DIR="${1:-./backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

cd "$(dirname "$0")/../.."
mkdir -p "$OUT_DIR"

# Load POSTGRES_* from .env without exporting secrets to the shell history.
set -a
# shellcheck disable=SC1091
[[ -f .env ]] && source .env
set +a

PG_USER="${POSTGRES_USER:-noblen}"
PG_DB="${POSTGRES_DB:-noblen}"
OUT_FILE="${OUT_DIR}/noblen_${PG_DB}_${STAMP}.sql.gz"

echo "[backup] Dumping database '$PG_DB' -> $OUT_FILE"
docker compose -p "$PROJECT_NAME" exec -T postgres \
  pg_dump -U "$PG_USER" -d "$PG_DB" | gzip > "$OUT_FILE"

echo "[backup] Done: $OUT_FILE"
echo "[backup] Restore with:  gunzip -c $OUT_FILE | docker compose -p $PROJECT_NAME exec -T postgres psql -U $PG_USER -d $PG_DB"
