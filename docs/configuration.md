# Configuration Reference

See [guardian.example.yaml](../guardian.example.yaml) for a fully commented example.

## Top-Level Fields
- `flag_name`: Target Flagsmith feature flag name.
- `environment_key`: Flagsmith environment client key (or `FLAGSMITH_ENVIRONMENT_KEY` env var).
- `stages`: Ascending list of rollout percentages (e.g. `[1, 5, 25, 100]`).
- `soak_time_seconds`: Minimum healthy duration in each stage before advancing.
- `require_approval_before_final`: Require explicit human approval (`POST /approve`, CLI `guardian approve`, or Slack button) before the final stage.
- `require_winner`: When `true`, approval requires `chance_to_win >= 95%` and 95% CI low > 0.
- `reconcile_interval`, `echo_window_seconds`, `in_flight_grace_seconds`: Drift detection & write-ledger echo immunity windows.
- `segment` (optional): Flagsmith segment name for `SEGMENT_STAGE` before percentage stages.

## Guardrails
Each entry in `guardrails` specifies:
- `name`, `comparison` (`>`, `>=`, `<`, `<=`, `==`, `!=`), `threshold`, `min_samples`
- `metric_source`: `prometheus` | `push` | `flagsmith_experiment` | `datadog` | `cloudwatch`

Validate any config before starting:
```bash
guardian config validate --config guardian.example.yaml
```
