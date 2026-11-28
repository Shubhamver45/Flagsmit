"""Tests for new Guardian API endpoints: /pause, /resume, /incidents, /health, /ready, /flagsmith/*, and no token injection."""

from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from guardian.api import create_app
from guardian.config import RolloutConfig, Guardrail
from guardian.engine import GuardianEngine
from guardian.storage import GuardianStorage


def _build_test_client(tmp_path, api_token="test-secret-token"):
    storage = GuardianStorage(db_path=tmp_path / "api_new.db")
    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0
    mock_client.list_projects.return_value = [{"id": 1, "name": "Demo Project"}]
    mock_client.list_environments.return_value = [{"id": 1, "name": "Production", "api_key": "env-key"}]
    mock_client.list_features.return_value = [{"id": 10, "name": "new_checkout"}]

    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1.0, 5.0, 100.0],
        soak_time_seconds=30,
        guardrails=[
            Guardrail(
                name="error_rate",
                metric_source="prometheus",
                query="rate(errors[1m])",
                threshold=5.0,
                comparison=">",
            )
        ],
    )
    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=mock_client,
        metric_provider=lambda: {"error_rate": 0.1},
        storage=storage,
        skip_startup_reconciliation=True,
    )
    app = create_app(engine=engine, api_token=api_token)
    return TestClient(app), engine, storage


def test_index_does_not_inject_api_token(tmp_path, monkeypatch):
    monkeypatch.setenv("GUARDIAN_API_TOKEN", "super-secret-live-token-xyz")
    client, _, storage = _build_test_client(tmp_path, api_token="super-secret-live-token-xyz")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "super-secret-live-token-xyz" not in resp.text
    assert "Guardian" in resp.text
    storage.close()


def test_health_readiness_probe(tmp_path):
    client, _, storage = _build_test_client(tmp_path)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy", "service": "guardian"}

    ready_resp = client.get("/ready")
    assert ready_resp.status_code == 200
    data = ready_resp.json()
    assert data["service"] == "guardian"
    assert data["db"] == "ok"
    assert "flagsmith" in data
    storage.close()


def test_pause_resume_and_incidents_endpoints(tmp_path):
    token = "test-secret-token"
    headers = {"Authorization": f"Bearer {token}"}
    client, engine, storage = _build_test_client(tmp_path, api_token=token)

    # Unauthenticated protected endpoints must fail 401
    assert client.post("/pause").status_code == 401
    assert client.get("/incidents").status_code == 401
    assert client.get("/flagsmith/projects").status_code == 401
    assert client.get("/flagsmith/environments").status_code == 401
    assert client.get("/flagsmith/features").status_code == 401

    # Start rollout via /tick
    tick_resp = client.post("/tick", headers=headers)
    assert tick_resp.status_code == 200
    assert tick_resp.json()["current_state"] == "STAGE_1"

    # Pause
    pause_resp = client.post("/pause", headers=headers, json={"flag_name": "new_checkout"})
    assert pause_resp.status_code == 200
    assert pause_resp.json()["current_state"] == "PAUSED"
    assert pause_resp.json()["current_percentage"] == 1.0

    # Resume
    resume_resp = client.post("/resume", headers=headers, json={"flag_name": "new_checkout"})
    assert resume_resp.status_code == 200
    assert resume_resp.json()["current_state"] == "STAGE_1"

    # Incidents endpoint returns empty paginated list initially
    inc_resp = client.get("/incidents", headers=headers)
    assert inc_resp.status_code == 200
    assert inc_resp.json()["count"] == 0
    assert inc_resp.json()["total"] == 0

    # Discovery endpoints require auth and redact api_key from environments
    assert client.get("/flagsmith/projects", headers=headers).json()["count"] == 1
    env_resp = client.get("/flagsmith/environments", headers=headers).json()
    assert env_resp["count"] == 1
    assert "api_key" not in env_resp["environments"][0]
    assert client.get("/flagsmith/features", headers=headers).json()["count"] == 1
    storage.close()


def test_ineligible_approval_banner_and_guardrail_units(tmp_path):
    storage = GuardianStorage(db_path=tmp_path / "banner_units.db")
    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0

    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1.0, 25.0, 100.0],
        soak_time_seconds=0,
        require_approval_before_final=True,
        guardrails=[
            Guardrail(
                name="p95_latency_ms",
                metric_source="prometheus",
                query="histogram_quantile(0.95, latency)",
                threshold=250.0,
                comparison=">",
                unit="ms",
            ),
            Guardrail(
                name="checkout_ab_chance_to_be_worse",
                metric_source="flagsmith_experiment",
                threshold=10.0,
                comparison=">",
            ),
        ],
    )

    from guardian.metrics.base import MetricEvaluation, MetricStatus
    evals = [
        MetricEvaluation(
            name="p95_latency_ms",
            value=120.5,
            sample_size=200,
            status=MetricStatus.HEALTHY,
            is_breached=False,
            reason="Latency healthy",
        ),
        MetricEvaluation(
            name="checkout_ab_chance_to_be_worse",
            value=4.0,
            sample_size=120,
            status=MetricStatus.HEALTHY,
            is_breached=False,
            reason="Experiment healthy",
            details={
                "as_of": "2026-11-27T00:00:00Z",
                "srm_warning": True,
                "srm_p_value": 0.0001,
                "chance_to_be_worse": 4.0,
                "chance_to_win": 96.0,
            },
        ),
    ]

    class FakeProvider:
        def evaluate_all(self, guardrails):
            return evals

    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=mock_client,
        metric_provider=FakeProvider(),
        storage=storage,
        skip_startup_reconciliation=True,
    )
    # Advance to AWAITING_APPROVAL
    engine.tick()  # STAGE_1
    engine.tick()  # STAGE_2 (25%)
    engine.tick()  # AWAITING_APPROVAL

    app = create_app(engine=engine, api_token="tok")
    client = TestClient(app)

    status_data = client.get("/status").json()
    assert status_data["state"] == "AWAITING_APPROVAL"
    assert status_data["is_eligible_for_approval"] is False
    assert "Sample Ratio Mismatch" in status_data["approval_block_reason"]

    # Verify latency guardrail carries 'ms' unit, not '%'
    lat_g = next(g for g in status_data["guardrails"] if g["name"] == "p95_latency_ms")
    assert lat_g["unit"] == "ms"

    # Threshold, comparison and source must be present when metrics exist
    assert lat_g["threshold"] == 250.0
    assert lat_g["comparison"] == ">"
    assert lat_g["metric_source"] == "prometheus"
    exp_g = next(g for g in status_data["guardrails"] if g["name"] == "checkout_ab_chance_to_be_worse")
    assert exp_g["metric_source"] == "flagsmith_experiment"
    assert exp_g["threshold"] == 10.0

    # Verify dashboard HTML/JS disables banner approve button and updates banner text on !isEligible
    html = client.get("/").text
    assert 'id="awaiting-banner-text"' in html
    assert 'id="btn-banner-approve"' in html
    assert "bannerApproveBtn.disabled = !isAwaiting || !isEligible;" in html
    assert "strongEl.textContent = 'Approval Blocked:'" in html
    assert "g.unit !== undefined" in html
    # Block reasons / names are escaped or set via textContent, never raw innerHTML
    assert "</strong> ${blockReason}" not in html
    assert "esc(exp.block_reason)" in html
    assert "esc(g.name)" in html
    assert "esc(g.reason" in html
    storage.close()


def test_open_in_flagsmith_link_safe_and_no_secrets(tmp_path, monkeypatch):
    storage = GuardianStorage(db_path=tmp_path / "fs_link.db")
    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0
    mock_client.api_url = "http://flagsmith:8000/api/v1"
    mock_client._project_id = 7
    monkeypatch.setenv("FLAGSMITH_ADMIN_API_KEY", "secret-admin-key-xyz")
    monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "env-client-key-xyz")
    monkeypatch.delenv("GUARDIAN_FLAGSMITH_UI_URL", raising=False)

    cfg = RolloutConfig(flag_name="new_checkout", stages=[1.0, 100.0], soak_time_seconds=0)
    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=mock_client,
        metric_provider=lambda: {},
        storage=storage,
        skip_startup_reconciliation=True,
    )
    app = create_app(engine=engine, api_token="tok")
    client = TestClient(app)

    # 1. Unset: no Flagsmith URL in /status and no internal hostname leaked
    unset_resp = client.get("/status")
    assert unset_resp.status_code == 200
    assert "flagsmith_ui_url" not in unset_resp.json()
    assert "flagsmith:8000" not in unset_resp.text

    # 2. Disallowed scheme (e.g., javascript:): rejected
    monkeypatch.setenv("GUARDIAN_FLAGSMITH_UI_URL", "javascript:alert(1)")
    bad_resp = client.get("/status")
    assert "flagsmith_ui_url" not in bad_resp.json()

    # 3. Set to valid http(s) browser-facing URL: builds link without secrets or internal hostnames
    monkeypatch.setenv("GUARDIAN_FLAGSMITH_UI_URL", "http://localhost:8000")
    set_resp = client.get("/status")
    ui_url = set_resp.json().get("flagsmith_ui_url")
    assert ui_url == "http://localhost:8000/project/7/features"
    assert "secret-admin-key-xyz" not in set_resp.text
    assert "env-client-key-xyz" not in set_resp.text
    assert "flagsmith:8000" not in set_resp.text

    html = client.get("/").text
    assert 'id="open-flagsmith-link"' in html
    assert 'rel="noopener noreferrer"' in html
    assert "esc(fsUrl)" in html
    storage.close()
