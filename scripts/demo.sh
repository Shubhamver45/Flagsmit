#!/usr/bin/env bash
# ==============================================================================
# Guardian Demo Orchestrator
# Starts Flagsmith, ClickHouse, Prometheus, Demo App, Guardian, and Simulator
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

echo "========================================================================"
echo "🛡️  STARTING GUARDIAN DEMO ENVIRONMENT"
echo "========================================================================"

# 1. Start Docker Containers
echo "==> Verifying Docker services (Flagsmith, Postgres, ClickHouse, Prometheus, Demo App)..."
docker compose up -d

# 2. Wait for Services
echo "==> Waiting for service health checks..."

wait_for_url() {
    local url="$1"
    local name="$2"
    local max_retries="${3:-30}"
    local count=0
    until curl -s -f -o /dev/null "$url" || [ "$count" -ge "$max_retries" ]; do
        sleep 2
        count=$((count + 1))
    done
    if [ "$count" -ge "$max_retries" ]; then
        echo "❌ Timeout waiting for $name at $url"
        exit 1
    fi
    echo "  ✔ $name is ready ($url)"
}

wait_for_url "http://localhost:8000/api/v1/" "Flagsmith API" 30
wait_for_url "http://localhost:5001/health" "Demo Checkout App" 20
wait_for_url "http://localhost:9090/-/healthy" "Prometheus" 20

# 3. Cleanup Trap for Background Processes
PIDS=()
cleanup() {
    echo -e "\n==> Shutting down Guardian and Simulator background processes..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    echo "✔ Done. Note: Docker containers remain running (run ./scripts/down.sh to stop)."
}
trap cleanup EXIT INT TERM

# 4. Start Guardian Daemon
echo "==> Starting Guardian Rollout Daemon on http://localhost:8080..."
PYTHONPATH=. python3 -m guardian.main --port 8080 --interval 3 &
GUARDIAN_PID=$!
PIDS+=("$GUARDIAN_PID")

wait_for_url "http://localhost:8080/health" "Guardian Status API" 15

# 5. Start Traffic Simulator
echo "==> Starting continuous traffic simulator across 200 distinct users..."
PYTHONPATH=. python3 demo/simulator.py --continuous &
SIM_PID=$!
PIDS+=("$SIM_PID")

# 6. Summary Information Banner
echo ""
echo "========================================================================"
echo "🎉 DEMO ENVIRONMENT IS FULLY OPERATIONAL!"
echo "========================================================================"
echo "  • Guardian Live Dashboard : http://localhost:8080/"
echo "  • Flagsmith UI            : http://localhost:8000/ (admin@example.com / AdminPassword123!)"
echo "  • Demo App Checkout       : http://localhost:5001/"
echo "  • Prometheus Graph        : http://localhost:9090/"
echo ""
echo "Operator Commands:"
echo "  • Approve 100% Promotion  : ./scripts/guardian-approve.sh"
echo "                              OR python3 -m guardian.cli approve"
echo "  • Emergency Rollback      : python3 -m guardian.cli rollback --reason \"Manual abort\""
echo "  • Inspect Status JSON     : curl -s http://localhost:8080/status | jq ."
echo "========================================================================"
echo "Press Ctrl+C to stop Guardian and the Simulator."
echo ""

# Wait for children
wait
