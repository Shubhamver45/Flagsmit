# Flagsmith Guardian — Final Submission Evidence

## 1. Deployed Commit & Public Health Status
- **Public URL:** `https://flagsmith-guardian.onrender.com/`
- **Deployed Branch / Commit:** Render currently deploys `main` (`8399650`), **not** `feat/production-adapters` (`3c8e71b`). Proved by `GET /audit/verify` returning HTTP `404` on Render while `GET /health` returned `{"status":"healthy","service":"guardian"}` and `GET /ready` returned `{"status":"ready","service":"guardian","db":"ok","flagsmith":"ok"}`.
- **Render Config Fix Ready:** `feat/production-adapters` includes `config.render.yaml` (`metric_source: push`) and updated `render.yaml` + `Dockerfile` so Render will no longer hit `localhost:9090` once merged to `main`. See [deployment-verification.md](deployment-verification.md).

## 2. Docker Build Result
- `docker build -t flagsmith-guardian:final .` on `feat/production-adapters`: **PASSED** (`5.7s`, non-root `USER guardian` UID `10001`).

## 3. Unit Test Results
- `.venv/bin/python3 -m compileall -q guardian tests`: **PASSED** (exit `0`).
- `.venv/bin/python3 -m pytest -m "not live" -q`: **113 passed, 1 skipped, 1 deselected** (the 1 deselected test is `test_live_flagsmith_10_step_rollout_and_rollback` marked `@pytest.mark.live`).
- `PYTHONPATH=. .venv/bin/python3 scripts/check_readme_claims.py`: **PASSED**.
- `.venv/bin/python3 scripts/check_readme_links.py`: **PASSED**.

## 4. Live Integration Test Results
- `.venv/bin/python3 -m pytest -v -m live tests/test_flagsmith_live_integration.py` against live self-hosted Flagsmith (`http://localhost:8000/api/v1` via Docker Compose): **1 passed in 5.11s** (read feature state -> set `5%` allocation -> verified `5.0%` via Flagsmith Admin API -> injected push-metric breach -> triggered rollback -> verified `0.0%` via Flagsmith Admin API -> verified `ROLLBACK` audit entry + valid SHA-256 chain).
- *Note:* When run inside the clean-clone `compose.demo.yml` shell command (`task-11215`) without `.env` in `/Users/shubham/Desktop/Flagsmit`, `pytest -m live` reported `1 skipped` because `setup-flagsmith` had already exited; running with `setup_flagsmith.py` `.env` (`task-11153`) passed `1 passed in 5.11s`.

## 5. Experimentation & SRM Test Results
- `tests/test_experimentation_srm.py`: **4 passed** covering valid experiment results, insufficient sample size, missing exposure data, SRM detection (`p = 0.000002 < 0.001`), malformed experiment data (`MetricStatus.ERROR`), SRM blocking human approval (`ValueError`), and successful promotion (`PROMOTED`) after gates pass.
- Live Flagsmith Experimentation capture (`DEMO_FORCE_RECALC` unset): `411` enrolled, `> 99%` chance to win, `+163.6%` lift, `No SRM` (`p = 0.2175`).

## 6. Audit-Chain Test Results
- `tests/test_audit_chain.py`: **3 passed** covering all 7 scenarios:
  1. Concurrent writes (4 threads x 5 writes = 20 entries, `valid=True`)
  2. Process restart (close & reopen `GuardianStorage`, append & verify `valid=True`)
  3. Record modification (`UPDATE audit_log` -> `valid=False`)
  4. Record deletion (middle row deletion & tail row deletion -> `valid=False`)
  5. Record reordering (swapped entries -> `valid=False`)
  6. Invalid `chain_head` in `audit_meta` (`valid=False`)
  7. Repeated verification (idempotent deterministic results)
- **Architecture note:** Guarantees apply to a single-instance SQLite (`WAL` mode) deployment protected by `threading.RLock()`. Multi-instance clustering is not supported.

## 7. Authentication & Security Test Results
- `tests/test_security.py`: verified `localStorage` removed from `STATUS_PAGE_HTML`; `POST /auth/session` issues `HttpOnly`, `SameSite=Lax` `guardian_session` cookie (1h TTL) + `X-CSRF-Token` enforcement (`403` without CSRF header, `200` with valid CSRF header, `401` after `DELETE /auth/session`); CLI `Authorization: Bearer` preserved; chunked/no-`Content-Length` >64 KB request returns `413`; signed webhook replay with `X-Flagsmith-Timestamp` returns `409`.

## 8. Clean Installation Verification
- Fresh clone of `feat/production-adapters` in `/tmp/clean-final-test` with `docker compose -f compose.demo.yml up --build`:
  - Started `postgres`, `migrate-db`, `flagsmith`, `flagsmith-task-processor`, `prometheus`, `demo-app`, `setup-flagsmith`, `guardian`, and `simulator`.
  - Verified `GET /health` (`200`), `GET /ready` (`200`), and `GET /flagsmith/features` (`200`, feature discovery), then tore down cleanly (`docker compose -f compose.demo.yml down -v`).

## 9. Remaining Limitations
- Single-instance SQLite (`WAL` mode); no horizontal multi-replica clustering.
- Self-hosted open-source Flagsmith focus; Flagsmith Cloud/Enterprise untested.
- Render free-tier web service has no persistent disk across redeploys, and `feat/production-adapters` is not merged to `main` yet (so Render still serves `main`).
- `helm` and `gitleaks` are not installed locally (`gitleaks` runs in GitHub Actions CI).

---

## Judge Evidence Matrix

| Criterion | Working Capability | Source Files | Test Evidence | Demo / Repro |
| :--- | :--- | :--- | :--- | :--- |
| **1. Use of Flagsmith** | Admin API rollout/rollback, Python SDK checkout app, Experimentation + SRM gate, HMAC webhooks, optional Segments | `guardian/flagsmith_client.py`, `guardian/metrics/flagsmith_exp.py`, `demo/app.py` | `tests/test_flagsmith_live_integration.py` (1 passed live), `tests/test_experimentation_srm.py` (4 passed) | `pytest -m live tests/test_flagsmith_live_integration.py` |
| **2. Impact** | Automated `0%` rollback on Prometheus/push/Datadog/CloudWatch breach; human approval gate; SRM blocks unsafe promotion | `guardian/engine.py`, `guardian/state_machine.py`, `guardian/actions/alerts.py` | `tests/test_guardian_engine.py`, `tests/test_push_metrics.py`, `tests/test_production_adapters.py` | `./scripts/demo.sh` |
| **3. Creativity** | Write-ledger echo immunity, SHA-256 audit hash chain (`audit_meta` head), `HttpOnly`+CSRF session, optional MCP server without approve tool | `guardian/storage.py`, `guardian/api.py`, `guardian/mcp_server.py` | `tests/test_audit_chain.py` (3 passed), `tests/test_security.py`, `tests/test_mcp_server.py` | `guardian audit verify` |
| **4. Demo & Reusability** | `2:47.8` demo video, `guardian doctor`/`init`/`config validate`, `guardian.example.yaml`, one-command `compose.demo.yml` | `guardian/cli.py`, `guardian.example.yaml`, `compose.demo.yml`, `docs/` | `tests/test_cli.py`, `scripts/check_readme_claims.py`, `scripts/check_readme_links.py` | `docker compose -f compose.demo.yml up --build` |
