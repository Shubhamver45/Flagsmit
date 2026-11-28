# Public Deployment Verification (`https://flagsmith-guardian.onrender.com/`)

Verified on **2026-11-28** against `https://flagsmith-guardian.onrender.com/`.

## Findings
- **Deployed commit / branch:** Render deploys from `main` (`8399650`), **not** `feat/production-adapters`. Verified by probing `GET /audit/verify`, which returned HTTP `404` on Render (whereas `/audit/verify` was added on `feat/production-adapters`).
- **Health endpoint (`GET /health`):** Returned HTTP `200`: `{"status":"healthy","service":"guardian"}`.
- **Readiness endpoint (`GET /ready`):** Returned HTTP `200`: `{"status":"ready","service":"guardian","db":"ok","flagsmith":"ok"}`.
- **Genuine backend state & Flagsmith connectivity:** `GET /status` returns live JSON from Guardian's SQLite state (`state: AWAITING_APPROVAL`, `percentage: 25.0`) and `GET /ready` confirms Flagsmith connectivity (`"flagsmith":"ok"`). However, because `main` uses `config.example.yaml` (`metric_source: prometheus` on `localhost:9090`), `new_flow_error_rate` reports `ERROR (metrics source unreachable)` on Render's single free container.
- **Persistent storage:** Render's free web service plan (`plan: free` in `render.yaml`) does **not** attach a persistent disk across container redeploys.
- **Rollback confirmation:** Guardian's rollback state (`ROLLED_BACK`) is only committed after `FlagsmithAdminClient.rollback_to_zero()` succeeds against the Flagsmith Admin API; it is not inferred from an unverified HTTP request.
- **Prepared fix (on `feat/production-adapters`):** Added `config.render.yaml` (`metric_source: push`) and updated `render.yaml` + `Dockerfile` so Render will use push metrics once `feat/production-adapters` is merged to `main`.
