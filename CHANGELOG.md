# Changelog

All notable changes to the Guardian project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-28

### Added
- **Automated Phased Delivery Engine**: Built state machine advancing flags through configurable stages (1% ➔ 5% ➔ 25% ➔ 100%) with soak duration enforcement, plus `PAUSED` rollout state (`POST /pause`, `POST /resume`).
- **Flagsmith Integration**:
  - Multivariate flag rollout weight manipulation via Flagsmith Admin REST API (`PATCH /api/v1/features/featurestates/{id}/`) with HTTP retry/timeouts and project/environment/feature discovery endpoints (`/flagsmith/*`).
  - Native Flagsmith Experimentation metric ingestion (Bayesian chance-to-win, 95% credible intervals, and chance-to-be-worse).
  - Sample Ratio Mismatch (SRM) anomaly detector ($p < 0.001$) blocking promotion when traffic assignment is corrupt.
  - Inbound webhook handler (`POST /webhooks/flagsmith`) with cryptographic `X-Flagsmith-Signature` HMAC-SHA256 validation.
- **Optional Features (Off by Default)**:
  - **Push Metrics Source** (`POST /metrics/ingest`, `metric_source: push`): Rolling-window error rate evaluation without requiring Prometheus.
  - **MCP Server** (`pip install flagsmith-guardian[mcp]`, `guardian mcp`): Stdio MCP tools (`get_rollout_status`, `start_rollout`, `pause`, `resume`, `rollback`, `request_approval`) while forbidding autonomous 100% approval.
  - **Segment Stage** (`segment:` config): Optional pre-percentage rollout stage targeting a Flagsmith segment before percentage splits.
- **Safety Breakers, Incident Intelligence & Instant Rollbacks**:
  - Continuous Prometheus PromQL or push-metric guardrail evaluations triggering automated rollback to 0% within seconds of breach.
  - Human approval gate prior to final 100% promotion.
  - Structured Incident Intelligence (`guardian/incident.py`, `GET /incidents`) and `/ready` readiness probe.
- **Drift Detection & Persistence**:
  - Dual-tier out-of-band drift detection combining real-time webhooks with periodic background reconciliation polling.
  - Write ledger echo immunity (`write_ledger` table in SQLite) eliminating false drift alerts from delayed Flagsmith webhook deliveries.
  - Single-file SQLite storage (`guardian.db`) persisting state, stage entered timestamps, audit decisions, and experiment snapshots across daemon and container restarts.
  - Multi-flag rollout orchestration supporting independent state machines and metrics per flag.
- **Production Security & Hardening**:
  - Strict production mode (`GUARDIAN_ENV=production`) enforcing non-zero startup refusal without `GUARDIAN_API_TOKEN` and fail-closed webhook validation.
  - Unprivileged non-root container execution (`USER guardian`, UID 10001) with read-only root filesystem support and dedicated `/app/data` volume.
  - Default loopback binding (`127.0.0.1`) preventing accidental public network exposure.
  - Request body size limits (64 KB) and sliding-window rate limiting on write endpoints.
  - Strict prohibition of credentials in URL query parameters.
  - Automated secret redaction filter preventing token and API key leakage into logs.
- **Operator Tooling, Packaging & Diagnostics**:
  - Responsive live web dashboard (`GET /`) across mobile, tablet, and desktop viewports.
  - Operator CLI (`guardian init`, `approve`, `rollback`, `reset`, `status`, `mcp`).
  - 5-minute BYOA guide (`docs/bring-your-own-app.md`) and example Kubernetes Helm chart (`deploy/kubernetes/`).
  - Automated end-to-end demonstration script (`scripts/demo.sh`) validating healthy rollout, drift hold, worse variation block, decisive winner clearance, fault rollback, and SRM anomaly detection.
