#!/usr/bin/env bash
set -euo pipefail

GUARDIAN_URL="${GUARDIAN_URL:-http://localhost:8080}"
curl -s -X POST -H "Content-Type: application/json" -d '{"approved_by":"operator","reason":"One-line CLI approval"}' "${GUARDIAN_URL}/approve"
echo ""
