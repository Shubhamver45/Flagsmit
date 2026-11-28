# Troubleshooting

1. **`guardian init` or `pip install -e .` fails on Python 3.9:**
   - Guardian requires Python `>=3.11`. Create your venv with `python3.12 -m venv .venv` (or `python3.11`).
2. **Stale Docker volumes cause Flagsmith `HTTP 500` on login during `./scripts/demo.sh`:**
   - Reset volumes cleanly: `docker compose down -v` (or `docker compose -f compose.demo.yml down -v`), then re-run.
3. **`metrics source unreachable` on Render or standalone Guardian:**
   - Prometheus on `localhost:9090` is not running inside a standalone container. Use `config.render.yaml` (`metric_source: push` via `POST /metrics/ingest`) or set `PROMETHEUS_URL` to a reachable Prometheus instance.
4. **Diagnostics:**
   - Run `guardian doctor` to check Python version, `config.yaml` validity, Flagsmith/Prometheus reachability, and auth env vars.
   - Verify audit log integrity: `guardian audit verify` (or `GET /audit/verify`).
