# Security Policy and Hardening Guide

## Vulnerability Reporting

If you discover any security issues, vulnerabilities, or potential exposures in Guardian or its deployment templates, please do not disclose them publicly.

- **Reporting Channel**: Open a confidential security advisory on GitHub or email the maintainer directly.
- **Response Commitment**: Initial acknowledgment within 48 hours, followed by a triaged mitigation or fix plan.
- **Scope**: Guardian core engine, CLI, REST API, webhook validation, and deployment container configs.

---

## Production Security vs. Demo Architecture

Guardian is engineered with strict separation between **production operations** and the **local accelerated demonstration sandbox**:

### 1. Production Mode (`GUARDIAN_ENV=production`)
When `GUARDIAN_ENV=production` is set, Guardian enforces strict defense-in-depth:
- **Mandatory API Authentication**: Guardian will refuse to boot if `GUARDIAN_API_TOKEN` is not set (`exit 1`).
- **Fail-Closed Webhook Ingress**: The webhook endpoint (`POST /webhooks/flagsmith`) strictly requires `FLAGSMITH_WEBHOOK_SECRET` to be configured and valid `X-Flagsmith-Signature` HMAC headers. Webhooks without a configured secret are rejected with `401 Unauthorized`.
- **Private Loopback Binding**: Defaults to `127.0.0.1` when run directly. In Docker, expose only to localhost via `-p 127.0.0.1:8080:8080` or an internal ingress reverse proxy.
- **Rate Limiting & Size Caps**: Write endpoints (`/approve`, `/rollback`, `/reset`, `/tick`, `/webhooks/flagsmith`) enforce sliding-window rate limiting (default 60 req/min) and request body size limits (max 64 KB).
- **Credentials in URLs Forbidden**: Passing tokens via query parameters (`?token=...`, `?key=...`) is strictly prohibited and returns `400 Bad Request`. Pass credentials only via headers (`Authorization: Bearer <token>` or `X-Guardian-Token`).
- **Non-Root Container**: The official Docker image runs as unprivileged user `guardian` (UID 10001) with a read-only root filesystem and dedicated writable volume at `/app/data` for SQLite.

### 2. Demo-Only Architecture and Endpoints
The following endpoints, features, and settings exist solely for local sandbox testing and developer demonstration:
- `/toggle-bug` & `/toggle-srm`: Exposed on the Demo Checkout App (`localhost:5001`) to simulate synthetic faults and sample ratio mismatches. Protected by optional `DEMO_API_TOKEN`.
- `/reset-metrics`: Resets in-memory simulated conversion metrics in the demo app.
- `DEMO_FORCE_RECALC=1`: A developer shortcut that invokes accelerated Flagsmith background recalculations during local demo runs, bypassing the 5-minute production cooldown.
- `host.docker.internal` & `SSRF_ALLOWED_HOSTS`: Configured in Docker Compose strictly to allow local webhook loopback between Docker containers and host processes during local demonstrations.

---

## Historical Commit Notice & Allowlisted Throwaway Keys

Early commits in this repository contain throwaway local keys, test passwords, and mock environment tokens generated during initial scaffolding on destroyed, ephemeral local test instances (such as local Flagsmith Docker containers running on `localhost`).
- **No Production Impact**: None of these keys grant access to any persistent, cloud, or external production systems.
- **Allowlist Policy**: Specific historic commit fingerprints are documented and allowlisted in `.gitleaksignore`.
- **Clean Configuration**: All active runtime deployments load credentials exclusively from environment variables or gitignored configuration files (`.env`, `config.yaml`). Default demo passwords are restricted strictly to clearly labelled demo files (`docker-compose.yml`, `scripts/demo.sh`).
