#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

echo "==> Starting Flagsmith, PostgreSQL, and ClickHouse..."
docker compose up -d

echo "==> Waiting for Flagsmith API to become healthy..."
until curl -sf http://localhost:8000/health/ > /dev/null 2>&1; do
    printf '.'
    sleep 2
done
echo " Ready!"

echo "==> Ensuring bootstrap setup (org, project, environment, master API key, warehouse)..."
docker compose cp "$DIR/scripts/setup_flagsmith.py" flagsmith:/app/setup_flagsmith.py
docker compose exec -T flagsmith python manage.py shell -c "exec(open('/app/setup_flagsmith.py').read()); setup()"

echo "==> Flagsmith stack is up and verified!"
