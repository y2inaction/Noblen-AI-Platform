#!/usr/bin/env bash
# Noblen AI Platform — production deploy helper (Ubuntu / AWS EC2).
#
# Safe-by-default: it only touches the isolated Noblen compose project and never
# stops or removes unrelated containers, volumes, or networks on the host.
#
# Usage (from the repo root on the server):
#   ./infra/scripts/deploy.sh
set -euo pipefail

PROJECT_NAME="noblen-ai-platform"
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.prod.yml)

cd "$(dirname "$0")/../.."

if [[ ! -f .env ]]; then
  echo "ERROR: .env not found. Copy .env.example to .env and set secrets first." >&2
  exit 1
fi

echo "[deploy] Inspecting existing containers (for awareness; nothing is stopped)..."
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}' || true

echo "[deploy] Pulling latest images and (re)building only Noblen services..."
docker compose -p "$PROJECT_NAME" "${COMPOSE_FILES[@]}" pull || true
docker compose -p "$PROJECT_NAME" "${COMPOSE_FILES[@]}" build

echo "[deploy] Starting the Noblen stack (isolated project: $PROJECT_NAME)..."
docker compose -p "$PROJECT_NAME" "${COMPOSE_FILES[@]}" up -d

echo "[deploy] Waiting for backend health..."
for _ in $(seq 1 30); do
  if docker compose -p "$PROJECT_NAME" "${COMPOSE_FILES[@]}" exec -T backend \
      curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    echo "[deploy] Backend healthy."
    break
  fi
  sleep 2
done

echo "[deploy] Done. Only the '$PROJECT_NAME' project was modified."
