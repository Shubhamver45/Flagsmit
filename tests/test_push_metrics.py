"""
Tests for the optional push metrics source (metric_source: "push") and POST /metrics/ingest.
Covers rolling-window math, auth enforcement, 64KB size limits, malformed payloads,
and an end-to-end rollback triggered by pushed metrics.
"""

from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from guardian.api import create_app
from guardian.config import Guardrail, RolloutConfig
from guardian.engine import GuardianEngine
from guardian.metrics.base import MetricStatus
from guardian.metrics.push import PushMetricSource
from guardian.metrics.registry import CompositeMetricProvider
from guardian.storage import GuardianStorage


def test_push_metric_rolling_window_math():
    now = [1000.0]
    src = PushMetricSource(clock=lambda: now[0])
    guardrail = Guardrail(
        name="checkout_errors",
        metric_source="push",
        metric="checkout",
        comparison=">",
        threshold=5.0,
        min_samples=30,
        window_seconds=60.0,
    )

    # 1. Insufficient samples (< 30) -> INSUFFICIENT_DATA
    src.ingest(metric="checkout", requests=20, errors=5, timestamp=980.0)
    ev1 = src.evaluate(guardrail)
    assert ev1.status == MetricStatus.INSUFFICIENT_DATA
    assert ev1.sample_size == 20
    assert ev1.is_breached is False

    # 2. Add healthy batch inside window -> total 120 req, 5 err => 4.1667% <= 5.0%
    src.ingest(metric="checkout", requests=100, errors=0, timestamp=995.0)
    ev2 = src.evaluate(guardrail)
    assert ev2.status == MetricStatus.HEALTHY
    assert ev2.sample_size == 120
    assert abs(ev2.value - (5 / 120) * 100.0) < 1e-6
    assert ev2.is_breached is False

    # 3. Advance clock so first batch (980.0) expires (< 1050 - 60 = 990)
    #    Add new failing batch at 1045.0: 50 req, 10 err => window has 150 req, 10 err = 6.67% > 5.0%
    now[0] = 1050.0
    src.ingest(metric="checkout", requests=50, errors=10, timestamp=1045.0)
    ev3 = src.evaluate(guardrail)
    assert ev3.status == MetricStatus.BREACH
    assert ev3.sample_size == 150
    assert abs(ev3.value - (10 / 150) * 100.0) < 1e-6
    assert ev3.is_breached is True


def _make_push_app(tmp_path, api_token="secret-push-token"):
    storage = GuardianStorage(db_path=tmp_path / "push.db")
    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0

    push_src = PushMetricSource()
    provider = CompositeMetricProvider()
    provider.register_source("push", push_src)

    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1, 25, 100],
        soak_time_seconds=0,
        guardrails=[
            Guardrail(
                name="checkout_push_error_rate",
                metric_source="push",
                metric="checkout",
                comparison=">",
                threshold=5.0,
                min_samples=20,
                window_seconds=60.0,
            )
        ],
    )
    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=mock_client,
        metric_provider=provider,
        storage=storage,
        skip_startup_reconciliation=True,
    )
    app = create_app(engine=engine, api_token=api_token)
    return app, engine, mock_client, storage


def test_ingest_auth_oversized_and_malformed_payloads(tmp_path):
    app, _, _, storage = _make_push_app(tmp_path)
    client = TestClient(app)
    try:
        # 1. Auth required
        r_no_auth = client.post("/metrics/ingest", json={"metric": "checkout", "requests": 10, "errors": 0})
        assert r_no_auth.status_code in (401, 403)

        r_bad_auth = client.post(
            "/metrics/ingest",
            headers={"Authorization": "Bearer wrong-token"},
            json={"metric": "checkout", "requests": 10, "errors": 0},
        )
        assert r_bad_auth.status_code in (401, 403)

        # 2. Oversized (> 64KB) rejected with 413
        oversized = '{"metric":"checkout","requests":10,"errors":0,"pad":"' + ("x" * 70000) + '"}'
        r_big = client.post(
            "/metrics/ingest",
            headers={"Authorization": "Bearer secret-push-token", "Content-Type": "application/json"},
            content=oversized,
        )
        assert r_big.status_code == 413

        # 3. Malformed payloads rejected with 400
        bad_payloads = [
            "not-json",
            "[]",
            '{"requests": 10, "errors": 0}',
            '{"metric": "", "requests": 10, "errors": 0}',
            '{"metric": "checkout", "requests": -1, "errors": 0}',
            '{"metric": "checkout", "requests": 10, "errors": -2}',
            '{"metric": "checkout", "requests": 10, "errors": 15}',
            '{"metric": "checkout", "requests": true, "errors": 0}',
            '{"metric": "checkout", "requests": "10", "errors": 0}',
        ]
        for body in bad_payloads:
            r_bad = client.post(
                "/metrics/ingest",
                headers={"Authorization": "Bearer secret-push-token", "Content-Type": "application/json"},
                content=body,
            )
            assert r_bad.status_code == 400, f"Expected 400 for {body!r}, got {r_bad.status_code}"

        # 4. Valid payload succeeds
        r_ok = client.post(
            "/metrics/ingest",
            headers={"Authorization": "Bearer secret-push-token"},
            json={"metric": "checkout", "requests": 120, "errors": 4},
        )
        assert r_ok.status_code == 200
        assert r_ok.json()["status"] == "ingested"
        assert r_ok.json()["requests"] == 120
        assert r_ok.json()["errors"] == 4
    finally:
        storage.close()


def test_rollback_triggered_by_pushed_metrics(tmp_path):
    app, engine, mock_client, storage = _make_push_app(tmp_path)
    client = TestClient(app)
    try:
        # Advance PENDING -> STAGE_1 (1%)
        engine.tick()
        assert engine.state_machines["new_checkout"].current_state == "STAGE_1"

        # Push healthy counts (4 / 120 = 3.33% <= 5.0%)
        r1 = client.post(
            "/metrics/ingest",
            headers={"Authorization": "Bearer secret-push-token"},
            json={"metric": "checkout", "requests": 120, "errors": 4},
        )
        assert r1.status_code == 200
        engine.tick()
        assert engine.state_machines["new_checkout"].current_state != "ROLLED_BACK"

        # Push failing counts (20 / 50 -> window total 24 / 170 = 14.12% > 5.0% threshold)
        r2 = client.post(
            "/metrics/ingest",
            headers={"Authorization": "Bearer secret-push-token"},
            json={"metric": "checkout", "requests": 50, "errors": 20},
        )
        assert r2.status_code == 200
        engine.tick()

        sm = engine.state_machines["new_checkout"]
        assert sm.current_state == "ROLLED_BACK"
        assert sm.current_percentage == 0.0
        mock_client.rollback_to_zero.assert_called_once()
        incidents = storage.get_incidents(flag_name="new_checkout")
        assert len(incidents) == 1
        assert "checkout_push_error_rate" in (incidents[0].get("trigger") or incidents[0].get("explanation", ""))
    finally:
        storage.close()
