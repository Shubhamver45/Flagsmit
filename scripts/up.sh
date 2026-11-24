#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

echo "==> Starting Flagsmith, PostgreSQL, Task Processor, and Prometheus..."
docker compose up -d

echo "==> Waiting for Flagsmith API to become healthy..."
until curl -sf http://localhost:8000/health/ > /dev/null 2>&1; do
    printf '.'
    sleep 2
done
echo " Ready!"

echo "==> Ensuring bootstrap setup (org, project, environment, flags, master API key, warehouse)..."
python3 "$DIR/scripts/setup_flagsmith.py"

echo "==> Flagsmith stack is up and verified!"
