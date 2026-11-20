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


def test_cli_call_api_success():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "success", "message": "Promotion approved successfully", "current_state": "PROMOTED", "current_percentage": 100.0}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = cli.call_api("/approve", base_url="http://localhost:8080", data={"reason": "test"})
        assert res["status"] == "success"
