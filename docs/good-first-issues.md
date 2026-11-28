# Suggested Good First Issues

Ideas for contributors looking to get started with Flagsmith Guardian (not yet filed on GitHub):

1. **Add `--url` flag to `guardian status` CLI**
   - Allow `guardian status --url http://host:port` in `guardian/cli.py` (currently configured only via `GUARDIAN_URL`), with unit tests in `tests/test_cli.py`.
2. **Add Datadog / CloudWatch read-only metric adapter skeleton**
   - Implement a new `MetricSource` plugin under `guardian/metrics/` following `guardian/metrics/base.py` and `guardian/metrics/prometheus.py`, with mocked HTTP tests.
3. **Export Prometheus `/metrics` endpoint from Guardian itself**
   - Expose Guardian rollout state, current percentage, soak elapsed seconds, and rollback count as Prometheus gauges for monitoring Guardian.
4. **Add JSON output flag (`--json`) to `guardian status`**
   - Print raw `GET /status` JSON cleanly for scripting and CI pipelines.
5. **Add dark/light theme toggle or filter-by-state on multi-rollout dashboard**
   - Enhance `guardian/status_page.py` to filter the multi-rollout overview by state (`AWAITING_APPROVAL`, `ROLLED_BACK`, `PAUSED`) while preserving mobile/tablet responsiveness.
