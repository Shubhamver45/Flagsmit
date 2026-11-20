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
