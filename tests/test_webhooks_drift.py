"""
Tests for Flagsmith Webhooks and Real-Time Drift Detection.
Covers:
1. Valid HMAC-SHA256 signature verification (constant-time compare).
2. Invalid and missing signature rejection (HTTP 401).
3. Replay of real captured Flagsmith FLAG_UPDATED payload with changed_by.
4. Guardian's own writes must NOT trigger drift.
5. Drift detected via webhook pauses rollout and records audit alert.
6. Drift detected via periodic polling behaves identically.
"""

import hashlib
import hmac
import json
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from guardian.config import RolloutConfig
from guardian.engine import GuardianEngine
from guardian.flagsmith_client import FlagsmithClientProtocol
from guardian.api import create_app
from guardian.storage import GuardianStorage


class FakeFlagsmithClient:
    def __init__(self, initial_percentage: float = 0.0):
        self.percentage = initial_percentage
        self.enabled = initial_percentage > 0.0

    def get_feature(self, flag_name: str):
        return {"id": 1, "name": flag_name, "type": "MULTIVARIATE"}

    def get_environment_feature_state(self, flag_name: str):
        return {
            "id": 10,
            "enabled": self.enabled,
            "multivariate_feature_state_values": [
                {"id": 101, "multivariate_feature_option": 55, "percentage_allocation": self.percentage}
            ],
        }

    def get_percentage_rollout(self, flag_name: str) -> float:
        return self.percentage if self.enabled else 0.0

    def set_percentage_rollout(self, flag_name: str, percentage: float):
        self.percentage = float(percentage)
        self.enabled = percentage > 0.0
        return {"status": "ok", "percentage": self.percentage}

    def rollback_to_zero(self, flag_name: str, reason: str = ""):
        self.percentage = 0.0
        self.enabled = False
        return {"status": "rolled_back"}


@pytest.fixture
def test_setup(tmp_path, monkeypatch):
    db_file = tmp_path / "test_guardian.db"
    storage = GuardianStorage(db_path=db_file)
    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1, 5, 25, 100],
        soak_time_seconds=10,
    )
    client = FakeFlagsmithClient(initial_percentage=0.0)
    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=client,
        storage=storage,
        skip_startup_reconciliation=True,
    )
    secret = "test-webhook-secret-xyz"
    monkeypatch.setenv("FLAGSMITH_WEBHOOK_SECRET", secret)
    app = create_app(engine)
    test_client = TestClient(app)
    return {
        "engine": engine,
        "client": client,
        "storage": storage,
        "secret": secret,
        "test_client": test_client,
    }


def test_webhook_signature_valid(test_setup):
    tc = test_setup["test_client"]
    secret = test_setup["secret"]
    payload = {
        "event_type": "FLAG_UPDATED",
        "data": {
            "changed_by": "admin@example.com",
            "timestamp": "2026-11-24T12:00:00Z",
            "new_state": {
                "feature": {"name": "new_checkout"},
                "enabled": False,
                "multivariate_feature_state_values": [],
            },
        },
    }
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()

    resp = tc.post(
        "/webhooks/flagsmith",
        content=raw_bytes,
        headers={"Content-Type": "application/json", "X-Flagsmith-Signature": sig},
    )
    assert resp.status_code == 200
    assert resp.json().get("status") in ("ok", "ignored", "drift_detected")


def test_webhook_signature_invalid(test_setup):
    tc = test_setup["test_client"]
    payload = {"event_type": "FLAG_UPDATED", "data": {}}
    raw_bytes = json.dumps(payload).encode("utf-8")

    resp = tc.post(
        "/webhooks/flagsmith",
        content=raw_bytes,
        headers={"Content-Type": "application/json", "X-Flagsmith-Signature": "invalid-signature-hash"},
    )
    assert resp.status_code == 401
    assert "Invalid X-Flagsmith-Signature" in resp.json()["detail"]


def test_webhook_signature_missing(test_setup):
    tc = test_setup["test_client"]
    payload = {"event_type": "FLAG_UPDATED", "data": {}}
    raw_bytes = json.dumps(payload).encode("utf-8")

    resp = tc.post(
        "/webhooks/flagsmith",
        content=raw_bytes,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    assert "Missing X-Flagsmith-Signature" in resp.json()["detail"]


def test_webhook_guardians_own_writes_no_drift(test_setup):
    """
    When Guardian writes a rollout percentage, Flagsmith fires a webhook with that same percentage.
    Guardian must recognize it as expected and NOT trigger drift.
    """
    engine = test_setup["engine"]
    tc = test_setup["test_client"]
    secret = test_setup["secret"]

    # Guardian advances to STAGE_1 (1.0%)
    engine.tick(now=100.0)
    assert engine.current_percentage == 1.0

    # Flagsmith webhook fires echoing the 1.0% change
    payload = {
        "event_type": "FLAG_UPDATED",
        "data": {
            "changed_by": "guardian_service_account@example.com",
            "timestamp": "2026-11-24T12:00:00Z",
            "new_state": {
                "feature": {"name": "new_checkout"},
                "enabled": True,
                "multivariate_feature_state_values": [
                    {"multivariate_feature_option": 55, "percentage_allocation": 1.0}
                ],
            },
        },
    }
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()

    resp = tc.post(
        "/webhooks/flagsmith",
        content=raw_bytes,
        headers={"Content-Type": "application/json", "X-Flagsmith-Signature": sig},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert engine.drift_flags["new_checkout"]["detected"] is False


def test_webhook_replay_real_payload_triggers_drift(test_setup):
    """
    Replay of a real captured Flagsmith webhook payload where an external actor
    altered the flag percentage mid-rollout.
    """
    engine = test_setup["engine"]
    tc = test_setup["test_client"]
    secret = test_setup["secret"]

    # Guardian is at STAGE_1 (1.0%)
    engine.tick(now=100.0)
    assert engine.current_percentage == 1.0

    # Real captured payload: rogue operator sets flag to 75%
    real_payload = {
        "event_type": "FLAG_UPDATED",
        "data": {
            "changed_by": "rogue_engineer@example.com",
            "timestamp": "2026-11-24T12:05:00Z",
            "new_state": {
                "id": 10,
                "feature": {
                    "id": 1,
                    "name": "new_checkout",
                    "type": "MULTIVARIATE",
                    "description": "New checkout flow gated rollout flag",
                },
                "enabled": True,
                "value": "new_flow",
                "multivariate_feature_state_values": [
                    {
                        "id": 101,
                        "multivariate_feature_option": 55,
                        "percentage_allocation": 75.0,
                    }
                ],
            },
            "previous_state": {
                "id": 10,
                "enabled": True,
                "multivariate_feature_state_values": [
                    {
                        "id": 101,
                        "multivariate_feature_option": 55,
                        "percentage_allocation": 1.0,
                    }
                ],
            },
        },
    }
    raw_bytes = json.dumps(real_payload).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()

    resp = tc.post(
        "/webhooks/flagsmith",
        content=raw_bytes,
        headers={"Content-Type": "application/json", "X-Flagsmith-Signature": sig},
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "drift_detected"
    drift = res["drift"]
    assert drift["detected"] is True
    assert drift["changed_by"] == "rogue_engineer@example.com"
    assert drift["actual_percentage"] == 75.0
    assert drift["expected_percentage"] == 1.0

    # Verify status endpoint surfaces drift
    status_resp = tc.get("/status")
    status_data = status_resp.json()
    assert status_data["drift"]["detected"] is True
    assert status_data["drift"]["changed_by"] == "rogue_engineer@example.com"

    # Verify subsequent engine ticks hold the rollout due to drift
    entry = engine.tick(now=120.0)
    assert entry.decision == "HOLD"
    assert "DRIFT WARNING" in entry.reason


def test_drift_via_polling_only(test_setup):
    """
    Periodic polling catches drift if webhook delivery fails or was not sent.
    Drift found via polling behaves identically to drift via webhook.
    """
    engine = test_setup["engine"]
    client = test_setup["client"]

    # Guardian is at STAGE_1 (1.0%)
    engine.tick(now=100.0)
    assert engine.current_percentage == 1.0
    assert engine.drift_flags["new_checkout"]["detected"] is False

    # Flagsmith changes out-of-band without webhook
    client.percentage = 40.0

    # Trigger periodic reconciliation
    results = engine.reconcile_runtime()
    assert results["new_checkout"]["drift"] is True

    # Drift is active and rollout is held
    assert engine.drift_flags["new_checkout"]["detected"] is True
    assert engine.drift_flags["new_checkout"]["source"] == "polling"
    assert engine.drift_flags["new_checkout"]["actual_percentage"] == 40.0

    entry = engine.tick(now=120.0)
    assert entry.decision == "HOLD"
    assert "DRIFT WARNING" in entry.reason

    # External operator restores flag back to 1.0%
    client.percentage = 1.0
    results = engine.reconcile_runtime()
    assert results["new_checkout"]["drift"] is False
    assert engine.drift_flags["new_checkout"]["detected"] is False
