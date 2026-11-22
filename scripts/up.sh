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

echo "==> Ensuring bootstrap setup (org, project, environment, flags, master API key, warehouse)..."
docker compose cp "$DIR/scripts/setup_flagsmith.py" flagsmith:/app/setup_flagsmith.py
SETUP_OUTPUT=$(docker compose exec -T flagsmith python manage.py shell -c "exec(open('/app/setup_flagsmith.py').read()); setup()")
echo "$SETUP_OUTPUT"
if echo "$SETUP_OUTPUT" | grep -q "=== DOTENV_START ==="; then
    echo "$SETUP_OUTPUT" | awk '/=== DOTENV_START ===/{flag=1;next}/=== DOTENV_END ===/{flag=0}flag' > "$DIR/.env"
    echo "✔ Auto-generated and wrote $DIR/.env with fresh credentials!"
fi

echo "==> Flagsmith stack is up and verified!"
