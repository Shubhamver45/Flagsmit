#!/usr/bin/env bash
set -euo pipefail

GUARDIAN_URL="${GUARDIAN_URL:-http://localhost:8080}"
AUTH_HEADER=()
if [ -n "${GUARDIAN_API_TOKEN:-}" ]; then
  AUTH_HEADER=(-H "Authorization: Bearer ${GUARDIAN_API_TOKEN}")
fi

curl -s -X POST -H "Content-Type: application/json" "${AUTH_HEADER[@]}" -d '{"approved_by":"operator","reason":"One-line CLI approval"}' "${GUARDIAN_URL}/approve"
echo ""
