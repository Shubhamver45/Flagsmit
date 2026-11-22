from __future__ import annotations
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from guardian.config import RolloutConfig
from guardian.audit_log import AuditLogger
from guardian.state_machine import RolloutState
from guardian.engine import GuardianEngine
from guardian.api import create_app
from guardian import cli


@pytest.fixture
def mock_engine(fake_flagsmith):
    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1, 10, 50, 100],
        soak_time_seconds=60,
        guardrails=[],
    )
    logger = AuditLogger()
    engine = GuardianEngine(config=cfg, flagsmith_client=fake_flagsmith, audit_logger=logger)
    return engine


def test_api_health_and_status(mock_engine):
    app = create_app(mock_engine)
    client = TestClient(app)

    # Test Status Page HTML
    res_page = client.get("/")
    assert res_page.status_code == 200
    assert "Guardian 🛡️" in res_page.text
    assert "<!DOCTYPE html>" in res_page.text

    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "healthy", "service": "guardian"}

    status_res = client.get("/status")
    assert status_res.status_code == 200
    data = status_res.json()
    assert data["state"] == RolloutState.PENDING.value
    assert data["percentage"] == 0.0


def test_api_approve_rejected_when_not_awaiting_approval(mock_engine):
    app = create_app(mock_engine)
    client = TestClient(app)

    # Initial state is PENDING
    res = client.post("/approve", json={"approved_by": "test-admin", "reason": "Looks good"})
    assert res.status_code == 400
    assert "Cannot approve rollout" in res.json()["detail"]


def test_api_approve_success_flow(mock_engine):
    app = create_app(mock_engine)
    client = TestClient(app)

    # Move to AWAITING_APPROVAL state
    mock_engine.state_machine.current_state = RolloutState.AWAITING_APPROVAL.value
    mock_engine.state_machine.current_stage_index = 2

    res = client.post("/approve", json={"approved_by": "alice", "reason": "Verified metrics"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["current_state"] == RolloutState.PROMOTED.value
    assert body["current_percentage"] == 100.0
    assert mock_engine.flagsmith_client.get_percentage_rollout("new_checkout") == 100.0


def test_api_manual_rollback(mock_engine):
    app = create_app(mock_engine)
    client = TestClient(app)

    # Set to stage 2 (10%)
    mock_engine.state_machine.current_state = "STAGE_2"
    mock_engine.state_machine.current_stage_index = 1
    mock_engine.flagsmith_client.set_percentage_rollout("new_checkout", 10.0)

    res = client.post("/rollback", json={"reason": "Emergency rollback"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "rolled_back"
    assert body["current_state"] == RolloutState.ROLLED_BACK.value
    assert body["current_percentage"] == 0.0
    assert mock_engine.flagsmith_client.get_percentage_rollout("new_checkout") == 0.0


def test_api_tick(mock_engine):
    app = create_app(mock_engine)
    client = TestClient(app)

    # Tick from PENDING -> STAGE_1 (1%)
    res = client.post("/tick")
    assert res.status_code == 200
    data = res.json()
    assert data["current_state"] == "STAGE_1"
    assert data["current_percentage"] == 1.0


def test_api_reset(mock_engine):
    app = create_app(mock_engine)
    client = TestClient(app)

    # Move to STAGE_1
    client.post("/tick")
    assert mock_engine.current_state == "STAGE_1"

    # Reset
    res = client.post("/reset")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "reset"
    assert data["current_state"] == RolloutState.PENDING.value
    assert data["current_percentage"] == 0.0


def test_cli_call_api_success():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "success", "message": "Promotion approved successfully", "current_state": "PROMOTED", "current_percentage": 100.0}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = cli.call_api("/approve", base_url="http://localhost:8080", data={"reason": "test"})
        assert res["status"] == "success"


def test_write_endpoints_require_token_when_configured(mock_engine):
    app = create_app(mock_engine, api_token="secret-token-123")
    client = TestClient(app)

    # 1. Read-only endpoints do NOT require auth
    assert client.get("/health").status_code == 200
    assert client.get("/status").status_code == 200
    assert client.get("/").status_code == 200

    # 2. Write endpoints without token -> 401 Unauthorized
    res_approve = client.post("/approve", json={"approved_by": "alice"})
    assert res_approve.status_code == 401
    assert "Unauthorized" in res_approve.json()["detail"]

    res_rollback = client.post("/rollback", json={"reason": "Manual"})
    assert res_rollback.status_code == 401
    assert "Unauthorized" in res_rollback.json()["detail"]

    res_reset = client.post("/reset")
    assert res_reset.status_code == 401
    assert "Unauthorized" in res_reset.json()["detail"]

    res_tick = client.post("/tick")
    assert res_tick.status_code == 401
    assert "Unauthorized" in res_tick.json()["detail"]

    # 3. Write endpoints with INVALID token -> 401 Unauthorized
    res_bad_bearer = client.post("/tick", headers={"Authorization": "Bearer wrong-token"})
    assert res_bad_bearer.status_code == 401

    res_bad_header = client.post("/tick", headers={"X-API-Token": "wrong-token"})
    assert res_bad_header.status_code == 401

    # 4. Write endpoints with VALID Bearer token -> success
    res_valid_bearer = client.post("/tick", headers={"Authorization": "Bearer secret-token-123"})
    assert res_valid_bearer.status_code == 200
    assert res_valid_bearer.json()["status"] == "ok"

    # 5. Write endpoints with VALID X-API-Token header -> success
    res_valid_header = client.post("/reset", headers={"X-API-Token": "secret-token-123"})
    assert res_valid_header.status_code == 200
    assert res_valid_header.json()["status"] == "reset"

    # 6. Write endpoints with VALID query parameter (?token=...) -> success
    res_valid_query = client.post("/tick?token=secret-token-123")
    assert res_valid_query.status_code == 200


def test_cli_call_api_with_token():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "success", "message": "OK"}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = cli.call_api("/approve", base_url="http://localhost:8080", data={"reason": "test"}, token="my-cli-token")
        assert res["status"] == "success"

        # Verify Authorization header was sent in Request
        req_arg = mock_urlopen.call_args[0][0]
        assert req_arg.headers.get("Authorization") == "Bearer my-cli-token"


def test_demo_app_auth_when_token_configured(monkeypatch):
    import sys
    # Provide mocks for optional demo app container dependencies
    if "prometheus_client" not in sys.modules:
        sys.modules["prometheus_client"] = MagicMock()
    if "flagsmith" not in sys.modules:
        sys.modules["flagsmith"] = MagicMock()

    import demo.app as demo_app

    # When DEMO_API_TOKEN is set
    monkeypatch.setattr(demo_app, "DEMO_API_TOKEN", "demo-secret")
    with demo_app.app.test_client() as client:
        # Unauthenticated calls fail with 401
        res = client.post("/toggle-bug?enabled=1")
        assert res.status_code == 401
        assert "Unauthorized" in res.get_json()["error"]

        res_reset = client.post("/reset-metrics")
        assert res_reset.status_code == 401

        # With Bearer token -> 200
        res_auth = client.post("/toggle-bug?enabled=1", headers={"Authorization": "Bearer demo-secret"})
        assert res_auth.status_code == 200
        assert res_auth.get_json()["bug_enabled"] is True

        # With query param token -> 200
        res_query = client.post("/reset-metrics?token=demo-secret")
        assert res_query.status_code == 200
        assert res_query.get_json()["status"] == "metrics_cleared"
