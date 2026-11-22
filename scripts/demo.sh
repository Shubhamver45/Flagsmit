#!/usr/bin/env bash
# ==============================================================================
# Guardian Demo Orchestrator
# Starts Flagsmith, ClickHouse, Prometheus, Demo App, Guardian, and Simulator.
# Executes the complete demo flow: healthy rollout -> approval -> fault -> rollback -> alert.
# Pass --serve to keep the services running indefinitely for manual dashboard exploration.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

SERVE_MODE=0
for arg in "$@"; do
    if [ "$arg" = "--serve" ] || [ "$arg" = "-s" ] || [ "$arg" = "--manual" ]; then
        SERVE_MODE=1
    fi
done

echo "========================================================================"
echo "🛡️  GUARDIAN END-TO-END DEMO ORCHESTRATOR"
echo "========================================================================"

wait_for_url() {
    local url="$1"
    local name="$2"
    local max_retries="${3:-45}"
    local count=0
    until curl -s -f -o /dev/null "$url" 2>/dev/null || [ "$count" -ge "$max_retries" ]; do
        sleep 2
        count=$((count + 1))
        printf "."
    done
    if [ "$count" -ge "$max_retries" ]; then
        echo -e "\n❌ Timeout waiting for $name at $url"
        exit 1
    fi
    echo -e "  ✔ $name is ready ($url)"
}

# 1. Start core infrastructure
echo "==> Step 1: Starting Flagsmith, PostgreSQL, ClickHouse, and Prometheus..."
docker compose up -d postgres clickhouse migrate-db flagsmith prometheus

echo "==> Waiting for Flagsmith API to become ready..."
wait_for_url "http://localhost:8000/api/v1/" "Flagsmith API" 60
wait_for_url "http://localhost:9090/-/healthy" "Prometheus" 30

# 2. Run Flagsmith bootstrap setup if .env or keys are missing
if [ ! -f ".env" ] || ! grep -q "FLAGSMITH_ADMIN_API_KEY=" .env || ! grep -q "FLAGSMITH_ENVIRONMENT_KEY=" .env; then
    echo "==> Step 2: Auto-provisioning Flagsmith (Org, Project, Environment, Flag, Master Key)..."
    python3 scripts/setup_flagsmith.py
else
    echo "==> Step 2: Existing configuration detected in .env"
fi

# Export keys from .env into current shell for docker-compose interpolation
set -a
# shellcheck disable=SC1091
source .env
set +a

# 3. Start Demo App with the provisioned environment key
echo "==> Step 3: Starting Demo Checkout App container..."
docker compose up -d demo-app
wait_for_url "http://localhost:5001/health" "Demo Checkout App" 30

# Reset demo app state: disable bug initially for healthy phase, clear counters
curl -s -X POST "http://localhost:5001/toggle-bug?enabled=0" > /dev/null || true
curl -s -X POST "http://localhost:5001/reset-metrics" > /dev/null || true

# 4. Cleanup Trap for Background Processes
PIDS=()
cleanup() {
    echo -e "\n==> Cleaning up Guardian and Simulator background processes..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    echo "✔ Background processes stopped."
}
trap cleanup EXIT INT TERM

# 5. Start Guardian Daemon (DEMO_MODE=1, soak: 3s per stage for fast demonstration)
echo "==> Step 4: Launching Guardian daemon on http://localhost:8080 (DEMO_MODE=1, soak: 3s)..."
DEMO_MODE=1 DEMO_SOAK_SECONDS=3 PYTHONPATH=. python3 -m guardian.main --port 8080 --interval 1 --demo-mode &
GUARDIAN_PID=$!
PIDS+=("$GUARDIAN_PID")
wait_for_url "http://localhost:8080/health" "Guardian Status API" 20

# 6. Start Traffic Simulator
echo "==> Step 5: Starting continuous traffic simulator (200 distinct user identities)..."
PYTHONPATH=. python3 demo/simulator.py --continuous &
SIM_PID=$!
PIDS+=("$SIM_PID")

echo ""
echo "========================================================================"
echo "🎉 ALL SYSTEMS READY — EXECUTING DEMO WORKFLOW"
echo "========================================================================"
echo "  • Guardian Live Dashboard : http://localhost:8080/"
echo "  • Flagsmith UI            : http://localhost:8000/ (admin@example.com / AdminPassword123!)"
echo "  • Demo Checkout App       : http://localhost:5001/"
echo "  • Prometheus Graph        : http://localhost:9090/"
echo "========================================================================"

if [ "$SERVE_MODE" -eq 1 ]; then
    echo "Running in SERVE mode. Press Ctrl+C to stop."
    wait
    exit 0
fi

# Automated Demonstration Workflow
get_status_field() {
    local field="$1"
    python3 -c "import urllib.request, json; s = json.loads(urllib.request.urlopen('http://localhost:8080/status', timeout=3).read()); print(s.get('$field', ''))" 2>/dev/null || echo ""
}

wait_for_state() {
    local target_state="$1"
    local max_wait="${2:-40}"
    local count=0
    printf "  ⏳ Waiting for Guardian state '%s'..." "$target_state"
    while [ "$count" -lt "$max_wait" ]; do
        local st
        st=$(get_status_field "state")
        local pct
        pct=$(get_status_field "percentage")
        if [ "$st" = "$target_state" ]; then
            echo " [OK: ${st} (${pct}%)]"
            return 0
        fi
        sleep 1
        count=$((count + 1))
        printf "."
    done
    echo " [TIMEOUT after ${max_wait}s; current: $(get_status_field "state")]"
    return 1
}

echo ""
echo "--- Phase 1: Healthy Rollout Progression ---"
echo "Flag 'new_checkout' is advancing through stages under healthy error rate (< 5.0%)..."
wait_for_state "STAGE_1" 20
wait_for_state "AWAITING_APPROVAL" 35

echo ""
echo "--- Phase 2: Human Operator Approval ---"
echo "Rollout reached 25% and is holding for human sign-off before 100% promotion."
echo "Executing: python3 -m guardian.cli approve --by operator --reason 'Pre-promotion health check passed'"
python3 -m guardian.cli approve --by "operator" --reason "Pre-promotion health check passed"
wait_for_state "PROMOTED" 10
echo "✔ Feature flag successfully promoted to 100%!"

echo ""
echo "--- Phase 3: Fault Injection (Failure Spike) ---"
echo "Injecting fault into new checkout flow via Demo App (BUG_IN_NEW_CHECKOUT=1)..."
curl -s -X POST "http://localhost:5001/toggle-bug?enabled=1" | python3 -m json.tool
echo "Traffic simulator is now receiving ~30% payment processing errors on new_flow."

echo ""
echo "--- Phase 4: Automated Breach Detection & Instant Rollback ---"
echo "Guardian is monitoring Prometheus PromQL guardrail: error_rate > 5.0%..."
wait_for_state "ROLLED_BACK" 30

echo ""
echo "========================================================================"
echo "🎉 DEMO RUN COMPLETED SUCCESSFULLY!"
echo "========================================================================"
echo "Final Rollout State: $(get_status_field "state")"
echo "Final Rollout %    : $(get_status_field "percentage")%"
echo ""
echo "Audit Decision Trail:"
python3 -c "
import urllib.request, json
data = json.loads(urllib.request.urlopen('http://localhost:8080/status').read())
for e in data.get('timeline', []):
    pct = e.get('numbers', {}).get('target_percentage', e.get('numbers', {}).get('current_percentage', ''))
    pct_str = f' ({pct}%)' if pct != '' else ''
    print(f\"  [{e.get('decision', 'INFO')}] {e.get('from_state', '?')} ➔ {e.get('to_state', '?')}{pct_str} | {e.get('reason', '')}\")
"
echo "========================================================================"
