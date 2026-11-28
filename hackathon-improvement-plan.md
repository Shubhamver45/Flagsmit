# Flagsmith Guardian — Hackathon Improvement Plan

Generated from Phase Zero repository audit (`feat/production-adapters` / `main` baseline).

---

## P0 — Security, Data Integrity & Broken Functionality

### 1. Chunked / Missing `Content-Length` Request Size Bypass
- **Existing implementation:** `RequestSizeLimitMiddleware` in `guardian/api.py` inspects only `request.headers.get("content-length")`.
- **Problem:** Chunked transfer-encoded requests (`Transfer-Encoding: chunked`) or requests omitting `Content-Length` bypass the 65,536-byte limit and can buffer arbitrarily large payloads in memory.
- **Severity:** **P0**
- **Proposed solution:** Stream `request.stream()` inside `RequestSizeLimitMiddleware` for write methods, aborting with `413 Payload Too Large` as soon as cumulative bytes exceed `max_content_length`, and caching the bounded body for downstream handlers.
- **Files affected:** `guardian/api.py`, `tests/test_security.py`
- **Verification method:** Automated ASGI/TestClient test sending a >64 KB request without `Content-Length` (chunked iterator) and asserting HTTP `413`.

### 2. Tamper-Evident Audit Trail
- **Existing implementation:** `audit_log` table in `guardian/storage.py` stores decisions, transitions, timestamps, and `numbers_json` without integrity chaining.
- **Problem:** Audit rows can be altered or deleted in SQLite without detection.
- **Severity:** **P0**
- **Proposed solution:** Add `prev_hash` and `entry_hash` (SHA-256 chain over `prev_hash|timestamp|flag_name|from_state|to_state|decision|reason|numbers_json`) to `audit_log`, plus `verify_audit_chain()` in `GuardianStorage` and `GET /audit/verify` + `guardian audit verify`.
- **Files affected:** `guardian/storage.py`, `guardian/api.py`, `guardian/cli.py`, `tests/test_audit_verification.py`
- **Verification method:** Unit test asserting valid chain verification passes and mutating a row in SQLite causes `verify_audit_chain()` to fail.

### 3. Webhook Replay Protection & CORS Policy
- **Existing implementation:** `POST /webhooks/flagsmith` verifies HMAC-SHA256 `X-Flagsmith-Signature`, and `FastAPI` has no explicit `CORSMiddleware` allowlist.
- **Problem:** Replayed signed webhook payloads are not bounded by timestamp/digest deduplication, and CORS policy is implicit.
- **Severity:** **P0**
- **Proposed solution:** Add bounded webhook digest replay protection + optional `X-Flagsmith-Timestamp` freshness window (300s) when sent, and configure `CORSMiddleware` via `GUARDIAN_CORS_ORIGINS` (defaulting to local dashboard origins, never wildcard with credentials).
- **Files affected:** `guardian/api.py`, `tests/test_security.py`
- **Verification method:** Security tests verifying replay rejection and CORS header enforcement.

---

## P1 — Core Flagsmith Integration & Adoption

### 4. Live Self-Hosted Flagsmith End-to-End Integration Test
- **Existing implementation:** Unit tests mock `FlagsmithAdminClient`; `scripts/demo.sh` exercises Flagsmith end-to-end via shell.
- **Problem:** No automated `pytest` integration test exercises the 10-step Flagsmith write -> verify -> metric breach -> rollback -> verify 0% -> audit sequence directly against a live Flagsmith instance.
- **Severity:** **P1**
- **Proposed solution:** Add `tests/test_flagsmith_live_integration.py` (`@pytest.mark.live`) executing the full 10-step verification against self-hosted Flagsmith (`http://localhost:8000`) and run it against the live Docker stack.
- **Files affected:** `tests/test_flagsmith_live_integration.py`
- **Verification method:** Run `pytest -m live tests/test_flagsmith_live_integration.py` against the local Flagsmith Docker Compose stack.

### 5. Reusable Developer CLI & `guardian.example.yaml`
- **Existing implementation:** `guardian/cli.py` provides `init`, `approve`, `status`, `rollback`, `reset`, `mcp`. `config.example.yaml` exists, not `guardian.example.yaml`.
- **Problem:** Missing operator/developer diagnostics and subcommands (`guardian doctor`, `guardian config validate`, `guardian flags list`, `guardian rollout status|pause|resume`, `guardian audit list`, `guardian demo`).
- **Severity:** **P1**
- **Proposed solution:** Add `guardian.example.yaml` with full inline documentation and implement the target CLI commands in `guardian/cli.py` backed by real config/API/Flagsmith checks.
- **Files affected:** `guardian.example.yaml`, `guardian/cli.py`, `tests/test_cli.py`
- **Verification method:** CLI tests + running `guardian config validate` and `guardian doctor` locally.

---

## P2 — Reliability & Developer Experience

### 6. Explicit Rollback & Operational State Reporting in Dashboard
- **Existing implementation:** `guardian/status_page.py` displays state badges and banners, including `AWAITING_APPROVAL` and `ROLLED_BACK`.
- **Problem:** Rollback actions and guardrail states should clearly distinguish `Rollback pending` / `Rollback confirmed` / `Failed` / `Monitoring` / `Paused` / `Guardrail breached` / `Approval required` / `Healthy` without showing green success when unconfirmed.
- **Severity:** **P2**
- **Proposed solution:** Add explicit status mapping and rollback confirmation indicator in `guardian/status_page.py` and `guardian/engine.py` status payload, plus an interactive Demo/Architecture guide panel for first-time visitors on Render/local.
- **Files affected:** `guardian/status_page.py`, `tests/test_api.py`
- **Verification method:** `pytest` API/UI checks + headless Chrome verification.

### 7. CI Pipeline Completeness
- **Existing implementation:** `.github/workflows/ci.yml` runs Gitleaks, `pytest`, README checks, and Docker build + `/health`.
- **Problem:** Missing explicit syntax/lint/type verification step in CI.
- **Severity:** **P2**
- **Proposed solution:** Add syntax/lint (`python -m compileall guardian tests` and `ruff` if configured or `flake8`/static checks) to `.github/workflows/ci.yml`.
- **Files affected:** `.github/workflows/ci.yml`
- **Verification method:** Run the exact CI commands locally and inspect GitHub Actions on push.

---

## P3 — Visual Polish & Documentation

### 8. Complete Documentation Suite
- **Existing implementation:** `README.md` + `docs/bring-your-own-app.md`, `docs/deploy.md`, `docs/mcp.md`, `docs/segment-stage.md`.
- **Problem:** Missing dedicated `docs/architecture.md`, `docs/quickstart.md`, `docs/configuration.md`, `docs/deployment.md`, `docs/troubleshooting.md`, `docs/security.md`, `docs/demo.md`.
- **Severity:** **P3**
- **Proposed solution:** Create all 7 docs grounded strictly in verified code/commands, add accurate Mermaid architecture diagram, and verify `scripts/check_readme_claims.py` and `scripts/check_readme_links.py`.
- **Files affected:** `README.md`, `docs/*.md`
- **Verification method:** `check_readme_claims.py` and `check_readme_links.py`.
