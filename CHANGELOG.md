# Changelog

All notable changes to the Guardian project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-25

### Added
- **Automated Phased Delivery Engine**: Built state machine advancing flags through configurable stages (1% ➔ 5% ➔ 25% ➔ 100%) with soak duration enforcement.
- **Flagsmith Integration**:
  - Multivariate flag rollout weight manipulation via Flagsmith Admin REST API (`PATCH /api/v1/features/featurestates/{id}/`).
  - Native Flagsmith Experimentation metric ingestion (Bayesian chance-to-win, 95% credible intervals, and chance-to-be-worse).
  - Sample Ratio Mismatch (SRM) anomaly detector ($p < 0.001$) blocking promotion when traffic assignment is corrupt.
  - Inbound webhook handler (`POST /webhooks/flagsmith`) with cryptographic `X-Flagsmith-Signature` HMAC-SHA256 validation.
- **Safety Breakers & Instant Rollbacks**:
  - Continuous Prometheus PromQL guardrail evaluations triggering automated rollback to 0% within seconds of breach.
  - Human approval gate prior to final 100% promotion.
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
- **Operator Tooling & Diagnostics**:
  - Live interactive web dashboard (`GET /`) with real-time SSE-style polling, drift banners, and audit log inspection.
  - Operator CLI (`python -m guardian.cli approve`, `rollback`, `reset`, `status`).
  - Automated end-to-end demonstration script (`scripts/demo.sh`) validating healthy rollout, drift hold, worse variation block, decisive winner clearance, fault rollback, and SRM anomaly detection.
