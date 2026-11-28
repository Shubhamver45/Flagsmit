# Flagsmith Guardian v0.1.0 (Beta) — Release Notes

## What Guardian Does

Flagsmith Guardian is an autonomous rollout safety controller for self-hosted Flagsmith. It advances multivariate feature flags through configured canary stages (e.g. `1% → 5% → 25% → 100%`), enforces soak timers, evaluates operational error-rate guardrails and Flagsmith Experimentation / Sample Ratio Mismatch (SRM) gates, detects out-of-band Flagsmith edits (drift) via HMAC-SHA256 webhooks, and rolls back to `0%` automatically on guardrail breach — while requiring explicit human approval before the final `100%` promotion.

## Requirements

- **Python `>=3.11`** (for local/CLI install) or **Docker** (`compose.demo.yml` / container image)
- **Self-hosted Flagsmith** instance (Admin API key + Environment key)
- **Metrics source (choose one):**
  - **Prometheus** (`PROMETHEUS_URL`) for PromQL error-rate and sample-size guardrails, **or**
  - **Push metrics source** (`metric_source: push` via authenticated `POST /metrics/ingest`, no Prometheus required)

## Optional Features (All Off by Default)

- **Push metrics endpoint** ([docs/bring-your-own-app.md](bring-your-own-app.md)): Authenticated `POST /metrics/ingest` (64 KB limit) computing rolling-window error rates without Prometheus.
- **MCP server** ([docs/mcp.md](mcp.md)): Optional extra (`pip install flagsmith-guardian[mcp]`, `guardian mcp`) exposing `get_rollout_status`, `start_rollout`, `pause`, `resume`, `rollback`, and `request_approval` (never approves `100%` promotion).
- **Segment stage** ([docs/segment-stage.md](segment-stage.md)): Optional `segment:` stage targeting an internal Flagsmith segment at `0%` before percentage stages begin.

## Known Limitations

- **Single-instance SQLite architecture:** Guardian uses a local SQLite database (`guardian.db`) and is designed as a single-instance sidecar, not a horizontally clustered service.
- **Self-hosted open-source Flagsmith focus:** Tested against self-hosted open-source Flagsmith; Flagsmith Cloud and Enterprise multi-tenant setups have not been evaluated.
- **Flagsmith Experimentation beta:** Flagsmith's Experimentation engine is in beta and enforces a 5-minute (300s) cooldown on public `POST /results/refresh/` calls.
- **Simulated demo traffic:** The bundled checkout demo app and `./scripts/demo.sh` use simulated client traffic for deterministic local verification.

## Reporting Issues

- Bug reports and feature requests: [GitHub Issues](https://github.com/Shubhamver45/flagsmith-guardian/issues)
- Installation help and questions: see [SUPPORT.md](../SUPPORT.md)
