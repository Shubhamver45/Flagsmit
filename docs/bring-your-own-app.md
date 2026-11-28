# Bring Your Own App (Without Prometheus)

Guardian supports an optional **push metrics** source (`metric_source: "push"`) so you can run guarded rollouts and automatic rollbacks without deploying Prometheus.

Your application (or a lightweight cron/sidecar) periodically pushes request and error counts to Guardian's authenticated `POST /metrics/ingest` endpoint. Guardian computes the error rate `(sum(errors) / sum(requests)) * 100` over a configurable rolling window (`window_seconds`).

---

## 1. Configure a Push Guardrail (`config.yaml`)

```yaml
flag_name: "new_checkout"
stages: [1, 5, 25, 100]
soak_time_seconds: 60
require_approval_before_final: true

guardrails:
  - name: "checkout_push_error_rate"
    metric_source: "push"
    metric: "checkout"          # matches the "metric" field sent to /metrics/ingest
    comparison: ">"
    threshold: 5.0              # rollback to 0% if error rate > 5.0%
    min_samples: 30             # require at least 30 requests in the window
    window_seconds: 60          # rolling window length in seconds
```

---

## 2. Push Request & Error Counts (`POST /metrics/ingest`)

`POST /metrics/ingest` uses the same authentication (`Authorization: Bearer <GUARDIAN_API_TOKEN>` or `X-API-Token`) and 64 KB request size limit as Guardian's other write endpoints.

### `curl` Example

```bash
curl -X POST "http://localhost:8080/metrics/ingest" \
  -H "Authorization: Bearer ${GUARDIAN_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"metric": "checkout", "requests": 120, "errors": 4}'
```

**Payload fields:**
- `metric` *(string, required)*: Metric identifier matching `guardrails[].metric` (or `name`).
- `requests` *(integer >= 0, required)*: Total requests in this batch.
- `errors` *(integer >= 0, `<= requests`, required)*: Failed requests in this batch.
- `timestamp` *(number, optional)*: Unix epoch seconds; defaults to Guardian's current server time.
