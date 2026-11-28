# Bring Your Own App in 5 Minutes

Connect any application to Flagsmith Guardian in four steps — using either **Prometheus PromQL** or Guardian's built-in **push metrics** endpoint (no Prometheus required).

---

## Step 1: Expose a Metric (Choose Prometheus or Push)

### Option A — Prometheus (`/metrics`)
Expose a Prometheus counter (e.g., `http_requests_total{status="500"}`) on your app's `/metrics` endpoint and point Prometheus at it, then set `PROMETHEUS_URL` for Guardian.

### Option B — Push Metrics (`POST /metrics/ingest`, No Prometheus Needed)
Periodically send request and error counts from your app (or a cron/sidecar) to Guardian's authenticated `POST /metrics/ingest` endpoint (64 KB body limit, `Authorization: Bearer <GUARDIAN_API_TOKEN>`):

```bash
curl -X POST "http://localhost:8080/metrics/ingest" \
  -H "Authorization: Bearer ${GUARDIAN_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"metric": "checkout", "requests": 120, "errors": 4}'
```

---

## Step 2: Write Your Guardrail Config (`config.yaml`)

For **push metrics**:
```yaml
flag_name: "new_checkout"
stages: [1, 5, 25, 100]
soak_time_seconds: 60
require_approval_before_final: true

guardrails:
  - name: "checkout_push_error_rate"
    metric_source: "push"
    metric: "checkout"
    comparison: ">"
    threshold: 5.0
    min_samples: 30
    window_seconds: 60
```

For **Prometheus**, use `metric_source: "prometheus"` with `query:` and `sample_size_query:` (see [config.example.yaml](../config.example.yaml)).

---

## Step 3: Run `guardian init` Against Your Own Flagsmith

Install Guardian (requires Python `>=3.11`) and run the setup wizard to generate your configuration against your Flagsmith instance:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
guardian init
```

Or run non-interactively and check status against a running Guardian (`GUARDIAN_URL` defaults to `http://localhost:8080`):
```bash
guardian init --non-interactive --skip-verify \
  --flagsmith-url http://localhost:8000/api/v1 \
  --admin-api-key "$FLAGSMITH_ADMIN_API_KEY" \
  --environment-key "$FLAGSMITH_ENVIRONMENT_KEY" \
  --flag-name new_checkout
guardian status
```

---

## Step 4: Set Up the Flagsmith Webhook (For Drift Detection)

In your Flagsmith Environment Settings → **Webhooks** → **Create Webhook**:
1. **URL:** `http://<your-guardian-host>:8080/webhooks/flagsmith`
2. **Secret:** Set to the same value as `FLAGSMITH_WEBHOOK_SECRET` in Guardian's `.env` (verified with `X-Flagsmith-Signature` HMAC-SHA256).
3. **Events:** Enable `FLAG_UPDATED`.

Start Guardian:
```bash
python3 -m guardian.main --host 127.0.0.1 --port 8080
```
