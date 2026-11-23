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

# Ensure no leftover background processes from previous interrupted runs
pkill -f "guardian.main" 2>/dev/null || true
pkill -f "demo/simulator.py" 2>/dev/null || true

# 1. Start core infrastructure
echo "==> Step 1: Starting Flagsmith, PostgreSQL, Task Processor, and Prometheus..."
docker compose up -d --remove-orphans postgres migrate-db flagsmith flagsmith-task-processor prometheus

echo "==> Waiting for Flagsmith API to become ready..."
wait_for_url "http://localhost:8000/api/v1/" "Flagsmith API" 60
wait_for_url "http://localhost:9090/-/healthy" "Prometheus" 30

# 2. Run Flagsmith bootstrap setup to ensure clean keys and experiment state
echo "==> Step 2: Auto-provisioning Flagsmith (Org, Project, Environment, Flag, Master Key)..."
python3 scripts/setup_flagsmith.py

# Export keys from .env into current shell for docker-compose interpolation
set -a
# shellcheck disable=SC1091
source .env
set +a

# 3. Start Demo App with the provisioned environment key
echo "==> Step 3: Starting Demo Checkout App container..."
docker compose up -d --build demo-app
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

# 5. Ensure Guardian configuration exists (from tracked config.demo.yaml or config.example.yaml)
if [ ! -f "config.yaml" ]; then
    echo "==> Generating config.yaml from config.demo.yaml..."
    cp config.demo.yaml config.yaml 2>/dev/null || cp config.example.yaml config.yaml
fi

# 6. Start Guardian Daemon (DEMO_MODE=1, soak: 3s per stage for fast demonstration)
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
echo "  • Flagsmith UI            : http://localhost:8000/ (admin@example.com / see README)"
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

# Helper functions for Flagsmith Experimentation Results
refresh_flagsmith_experiment() {
    if [ "${DEMO_FORCE_RECALC:-1}" = "1" ]; then
        echo "  [demo shortcut] DEMO_FORCE_RECALC=1: triggering accelerated recalculation via Flagsmith task processor..."
        docker compose exec -T flagsmith python manage.py shell -c "
from experimentation.tasks import compute_experiment_results
from experimentation.models import Experiment, ExperimentResults
from django.utils import timezone
exp = Experiment.objects.first()
if exp:
    compute_experiment_results(exp.id)
    ExperimentResults.objects.filter(experiment=exp).update(
        refresh_requested_at=timezone.now() - timezone.timedelta(minutes=6)
    )
" > /dev/null 2>&1 || true
        sleep 2
    else
        echo "  Triggering experiment refresh via public API (POST /results/refresh/)..."
        python3 -c "
import os, requests
from dotenv import dotenv_values
env = dotenv_values('.env')
url = f'{env.get(\"FLAGSMITH_URL\", \"http://localhost:8000/api/v1\")}/environments/{env.get(\"FLAGSMITH_ENVIRONMENT_KEY\", \"\")}/experiments/'
headers = {'Authorization': f'Api-Key {env.get(\"FLAGSMITH_ADMIN_API_KEY\", \"\")}'}
try:
    r = requests.get(url, headers=headers, timeout=5).json()
    if r.get('results'):
        exp_id = r['results'][0]['id']
        ref = requests.post(f'{url}{exp_id}/results/refresh/', headers=headers, timeout=5)
        if ref.status_code in (200, 202):
            print(f'  ✔ Refresh queued via public API (HTTP {ref.status_code})')
        elif ref.status_code == 429:
            print('  ℹ Cooldown active (HTTP 429); reading last computed results as designed')
        else:
            print(f'  Public refresh returned HTTP {ref.status_code}')
except Exception as e:
    print('  Public API refresh error:', e)
"
    fi
}

get_flagsmith_raw_results() {
    python3 -c "
import os, requests, json
from dotenv import dotenv_values
env = dotenv_values('.env')
url = f'{env.get(\"FLAGSMITH_URL\", \"http://localhost:8000/api/v1\")}/environments/{env.get(\"FLAGSMITH_ENVIRONMENT_KEY\", \"\")}/experiments/'
headers = {'Authorization': f'Api-Key {env.get(\"FLAGSMITH_ADMIN_API_KEY\", \"\")}'}
try:
    r = requests.get(url, headers=headers, timeout=5).json()
    if r.get('results'):
        exp_id = r['results'][0]['id']
        res = requests.get(f'{url}{exp_id}/results/', headers=headers, timeout=5).json()
        print(json.dumps(res, indent=2))
except Exception as e:
    print('Failed to read experiment results:', e)
"
}

print_guardian_experiment_status() {
    python3 -c "
import urllib.request, json
try:
    s = json.loads(urllib.request.urlopen('http://localhost:8080/status', timeout=3).read())
    exp = s.get('experiment')
    if exp:
        print(f\"  • Experiment Name   : {exp.get('name')}\")
        shortcut_tag = ' (demo shortcut)' if exp.get('demo_shortcut') else ''
        print(f\"  • Source Text       : results computed by Flagsmith from events sent by the demo app, as of {exp.get('as_of')}{shortcut_tag}\")
        cw = exp.get('chance_to_be_worse')
        cw_str = f\"{cw}%\" if cw is not None else \"n/a (collecting data)\"
        print(f\"  • Chance to be Worse: {cw_str}\")
        srm_p = exp.get('srm_p_value')
        if exp.get('srm_warning'):
            srm_str = f\"WARNING (Corrupt, p-value: {srm_p:.6f})\" if srm_p is not None else \"WARNING (Corrupt)\"
        elif srm_p is not None:
            srm_str = f\"Healthy (p-value: {srm_p})\"
        else:
            srm_str = \"Healthy (p-value: n/a (collecting data))\"
        print(f\"  • SRM Status        : {srm_str}\")
        print(f\"  • Collecting Data   : {exp.get('is_collecting_data')}\")
        print(f\"  • Approval Gate     : {'ELIGIBLE (Clear for 100% promotion)' if exp.get('is_eligible_for_approval') else 'BLOCKED: ' + str(exp.get('block_reason'))}\")
except Exception as e:
    print('  Experiment telemetry check:', e)
"
}

echo ""
echo "--- Phase 1: Progressive Rollout Advancing to Awaiting Approval ---"
echo "Flag 'new_checkout' is advancing through stages under healthy error rate (< 5.0%)..."
wait_for_state "STAGE_1" 20
wait_for_state "AWAITING_APPROVAL" 35

echo ""
echo "--- Phase 2 (Scenario 1): Approval Gate Blocks on Insufficient Data ---"
echo "Rollout reached 25% and is holding before 100% promotion."
echo "Flagsmith is actively collecting events (<50 identities/arm or <5 conversions)."
refresh_flagsmith_experiment
print_guardian_experiment_status

echo "Attempting operator approval while data is still collecting:"
if python3 -m guardian.cli approve --by "operator" --reason "Premature approval attempt" 2>&1; then
    echo "❌ Error: Approval should have been blocked!"
    exit 1
else
    echo "✔ As expected, operator approval is strictly blocked when Flagsmith is still collecting data."
fi

echo ""
echo "--- Phase 3: Clean Balanced Traffic & Human Operator Sign-off ---"
echo "Generating balanced conversion events (simulated conversion rates: ~65% control, ~95% variation)..."
PYTHONPATH=. python3 demo/simulator.py --requests 180 --identities 200 > /dev/null
refresh_flagsmith_experiment

echo ""
echo "==> Raw Flagsmith Experiment Results (Clean Statistical Inference):"
get_flagsmith_raw_results

echo ""
echo "==> Guardian Status Check (Gate Evaluation):"
print_guardian_experiment_status

echo "Executing operator promotion approval:"
python3 -m guardian.cli approve --by "operator" --reason "Pre-promotion health check passed: lift positive, no SRM"
wait_for_state "PROMOTED" 10
echo "✔ Feature flag successfully promoted to 100%!"

echo ""
echo "--- Phase 4: Fault Injection (Failure Spike) & Automated Rollback ---"
echo "Injecting fault into new checkout flow via Demo App (BUG_IN_NEW_CHECKOUT=1)..."
BUG_INJECT_TIME=$(python3 -c "import time; print(time.time())")
curl -s -X POST "http://localhost:5001/toggle-bug?enabled=1" | python3 -m json.tool
echo "Traffic simulator is now receiving ~30% payment processing errors on new_flow."
echo "Guardian is monitoring Prometheus PromQL guardrail: error_rate > 5.0%..."
wait_for_state "ROLLED_BACK" 30
ROLLBACK_TIME=$(python3 -c "import time; print(time.time())")
ELAPSED=$(python3 -c "print(round($ROLLBACK_TIME - $BUG_INJECT_TIME, 1))")
echo "✔ Automated rollback to 0% completed in ${ELAPSED}s!"

# Stop fault immediately so Prometheus rate window begins draining clean traffic
curl -s -X POST "http://localhost:5001/toggle-bug?enabled=0" > /dev/null

python3 -c "
import urllib.request, json
try:
    s = json.loads(urllib.request.urlopen('http://localhost:8080/status', timeout=3).read())
    rb_entry = None
    for e in s.get('timeline', []):
        if e.get('decision') == 'ROLLBACK':
            rb_entry = e
            break
    if rb_entry:
        val = rb_entry.get('numbers', {}).get('metric_value')
        val_str = f'{val:.2f}%' if isinstance(val, (int, float)) else str(val)
        print(f\"  • Breach Reason   : {rb_entry.get('reason')}\")
        print(f\"  • Observed Breach : {val_str} (threshold: 5.0%)\")
        print(f\"  • Time to Rollback: ${ELAPSED}s\")
    else:
        print(f\"  • Time to Rollback: ${ELAPSED}s (State: {s.get('state')})\")
    if s.get('slack_webhook_configured'):
        print(\"  • Slack Alert     : Slack alert sent to configured incoming webhook\")
    else:
        print(\"  • Slack Alert     : Slack alert skipped (no webhook set in .env)\")
except Exception as ex:
    print(f\"  • Rollback status check note: {ex}\")
"

echo ""
echo "--- Phase 5 (Scenario 2): SRM Anomaly Simulation & Blocked Approval Gate ---"
echo "Attempting operator re-arm via POST /reset (strictly rejected while guardrail is breaching)..."
MAX_REARM_RETRIES=30
REARM_COUNT=0
REARM_SUCCESS=0
while [ "$REARM_COUNT" -lt "$MAX_REARM_RETRIES" ]; do
    if RESET_OUT=$(python3 -m guardian.cli reset 2>&1); then
        echo -e "\n✔ Rollout successfully re-armed to PENDING (guardrails healthy)."
        REARM_SUCCESS=1
        break
    else
        if [ "$REARM_COUNT" -eq 0 ]; then
            REASON_LINE=$(echo "$RESET_OUT" | grep "Error" || echo "$RESET_OUT")
            echo "  ℹ Re-arm rejected as expected: $REASON_LINE"
            printf "  ⏳ Waiting for guardrail metric to recover before re-arm succeeds..."
        else
            printf "."
        fi
        sleep 1
        REARM_COUNT=$((REARM_COUNT + 1))
    fi
done

if [ "$REARM_SUCCESS" -ne 1 ]; then
    echo -e "\n❌ Timeout waiting for operator re-arm to succeed"
    exit 1
fi

echo "Advancing back to AWAITING_APPROVAL stage..."
wait_for_state "STAGE_1" 20
wait_for_state "AWAITING_APPROVAL" 35

echo "Simulating Sample Ratio Mismatch (SRM) by dropping exposure events for variation arm..."
curl -s -X POST "http://localhost:5001/toggle-srm?enabled=1" | python3 -m json.tool
echo "Sending skewed traffic to trigger real SRM in Flagsmith database..."
PYTHONPATH=. python3 demo/simulator.py --prefix srm_user --requests 450 --identities 400 > /dev/null
refresh_flagsmith_experiment

echo ""
echo "==> Raw Flagsmith Experiment Results (SRM Warning Detected):"
get_flagsmith_raw_results

echo ""
echo "==> Guardian Verification of SRM Anomaly:"
print_guardian_experiment_status

echo "Attempting operator approval while SRM warning is active:"
if python3 -m guardian.cli approve --by "operator" --reason "Approve despite SRM" 2>&1; then
    echo "❌ Error: Approval should have been blocked by SRM warning!"
    exit 1
else
    echo "✔ As expected, operator approval is strictly BLOCKED by Guardian when real SRM anomaly fires."
fi

curl -s -X POST "http://localhost:5001/toggle-srm?enabled=0" > /dev/null || true

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
