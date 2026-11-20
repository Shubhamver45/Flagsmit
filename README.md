# Guardian 🛡️
> Automated Guarded Rollouts for Self-Hosted Flagsmith (Hackathon Track 03: Build on Open Source Flagsmith)

Guardian is a Python service running alongside self-hosted Flagsmith that automates phased feature rollouts, monitors guardrail metrics, executes instant rollbacks on breaches, and triggers notifications via webhooks/Slack.

---

## Architecture & Components

- **Flagsmith (Self-Hosted)**: Feature flag engine, multivariate weighting, and Bayesian experimentation (`http://localhost:8000`).
- **ClickHouse Warehouse**: High-throughput telemetry and experimentation event store (`http://localhost:8123`, DB `flagsmith_exp`).
- **PostgreSQL**: Primary Flagsmith relational store.
- **Demo Checkout App (Flask)**: Gated checkout service evaluated with Flagsmith flag `new_checkout` (`http://localhost:5001`).
- **Prometheus**: Metric collection and scraping (`http://localhost:9090`).
- **Guardian Service (FastAPI / Python 3.12)**: The orchestration engine for phased rollouts and guardrail evaluations.

---

## Demo App & Traffic Simulator

The demo checkout app simulates an e-commerce checkout flow gated by Flagsmith feature flag `new_checkout`:
* **OFF (`old_flow`)**: Legacy checkout flow (100% success rate).
* **ON (`new_flow`)**: Modernized checkout flow with simulated failure mode (`BUG_IN_NEW_CHECKOUT=1` produces ~30% payment gateway failures).
* **Metrics**: Real-time Prometheus metrics exported at `http://localhost:5001/metrics` and scraped by Prometheus at `http://localhost:9090`.

### Running Traffic Simulator
```bash
python3 demo/simulator.py
```
This sends traffic across 200 distinct user identities and aggregates client-side vs. Prometheus-scraped error rates per flag variant.

---

## Getting Started

### 1. Prerequisites
- Docker & Docker Compose
- Python 3.12+

### 2. Start Flagsmith Stack
```bash
./scripts/up.sh
```
This script launches Postgres, ClickHouse, Flagsmith, and the Task Processor, runs database migrations, sets up the initial organisation, project, environment, Master Admin API key, and connects ClickHouse as the data warehouse.

### 3. Stop Flagsmith Stack
```bash
./scripts/down.sh
```

---

## Local Environment Details

- **Flagsmith Dashboard**: [http://localhost:8000](http://localhost:8000)
  - **Login**: `admin@example.com` / `AdminPassword123!`
- **Organisation**: `Guardian Org` (ID: `1`)
- **Project**: `Guardian Project` (ID: `1`)
- **Environment**: `Development` (ID: `1`)
- **Client Environment Key**: `N6LnMxtye9LM89yM7qwv8t`
- **Warehouse**: ClickHouse (`flagsmith_exp` at `clickhouse:8123`) — Status: **Connected**

---

## Guardian Core Engine

Guardian orchestrates phased rollouts using a deterministic state machine:
```text
PENDING ──> STAGE_1 (1%) ──> STAGE_2 (5%) ──> STAGE_3 (25%) ──> AWAITING_APPROVAL ──> PROMOTED (100%)
   │              │                 │                 │                   │
   └──────────────┴─────────────────┴─────────────────┴───────────────────┴──> ROLLED_BACK (0%)
                                 (on any metric breach)
```

### Key Modules
* [`guardian/config.py`](file:///Users/shubham/Desktop/Flagsmit/guardian/config.py): YAML configuration schema for stages (`[1, 5, 25, 100]`), soak duration, and guardrails.
* [`guardian/state_machine.py`](file:///Users/shubham/Desktop/Flagsmit/guardian/state_machine.py): State machine logic enforcing soak time, metric threshold breaches, and human-in-the-loop approval.
* [`guardian/flagsmith_client.py`](file:///Users/shubham/Desktop/Flagsmit/guardian/flagsmith_client.py): Admin API client for percentage rollouts and 0% rollbacks verified against self-hosted Flagsmith.
* [`guardian/audit_log.py`](file:///Users/shubham/Desktop/Flagsmit/guardian/audit_log.py): Append-only audit trail logging every decision, reason, and numerical evidence.
* [`guardian/metrics/`](file:///Users/shubham/Desktop/Flagsmit/guardian/metrics/): Unified metric source providers:
  * `PrometheusMetricSource`: PromQL evaluator with minimum sample size gating.
  * `FlagsmithExperimentMetricSource`: Bayesian chance-to-be-worse inference, SRM warning enforcement, and 300s refresh cooldown.
  * `CompositeMetricProvider`: Dispatcher routing guardrail queries across sources.
* [`guardian/engine.py`](file:///Users/shubham/Desktop/Flagsmit/guardian/engine.py): Unified orchestration engine.

### Running Unit Tests & Live Metric Verifications
```bash
# Run complete unit test suite (27 tests)
python3 -m pytest -v tests/

# Run live metric verification against running demo app and Prometheus
PYTHONPATH=. python3 scripts/test_live_metrics.py
```

---

## Actions, Alerts & Human Approval

Guardian automates defensive actions while keeping critical final rollout decisions under human control:

### 1. Automated Rollback to 0%
* When any guardrail metric breaches its threshold (e.g., error rate > 5.0%) or an Experiment detects a Sample Ratio Mismatch (SRM), Guardian immediately calls Flagsmith to set the multivariate rollout percentage back to 0.0% and disable the flag.

### 2. Alerts (Slack & Generic Webhooks)
* **Slack Webhook**: Formats decisions into rich Slack BlockKit notifications with status banners, reasons, and key numerical metrics.
* **Generic Webhook**: Posts standardized JSON payloads to any incident management or monitoring webhook URL (`POST /webhook`).
* **Idempotency Guarantee**: All actions and alert dispatches are protected by an `IdempotencyManager` (`guardian/actions/idempotency.py`). Repeated evaluation loops or service restarts will never double-fire rollbacks or re-send duplicate alerts for the same decision key.

### 3. Human Approval Step
Before promoting a flag to 100%, Guardian transitions into `AWAITING_APPROVAL` and holds. Operators approve the final promotion via:
* **FastAPI Endpoint**:
  ```bash
  curl -X POST http://localhost:8080/approve \
    -H "Content-Type: application/json" \
    -d '{"approved_by": "lead-sre", "reason": "Soak metrics verified healthy"}'
  ```
* **One-Line CLI Command**:
  ```bash
  python3 -m guardian.cli approve --by "lead-sre" --reason "Approved final rollout"
  ```
* **Or Convenience Shell Script**:
  ```bash
  ./scripts/guardian-approve.sh
  ```
* **Manual Operator Rollback**:
  ```bash
  python3 -m guardian.cli rollback --reason "Manual halt"
  ```

