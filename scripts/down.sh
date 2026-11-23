#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

echo "Stopping Flagsmith, Postgres, Task Processor, and Prometheus services..."
docker compose down "$@"
