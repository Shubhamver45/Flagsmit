"""Tests for multi-tenant user auth, workspace isolation, and push metrics."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from guardian.api import create_app
from guardian.audit_log import AuditLogger
from guardian.config import Guardrail, RolloutConfig
from guardian.engine import GuardianEngine
from guardian.metrics.push import PushMetricSource
from guardian.metrics.registry import CompositeMetricProvider
from guardian.storage import GuardianStorage


def _make_test_app(tmp_path):
    db_path = tmp_path / "guardian.db"
    storage = GuardianStorage(db_path=db_path)
    cfg = RolloutConfig(
        flag_name="new_checkout",
        environment_key="env_default",
        stages=[1, 5, 25, 100],
        soak_time_seconds=30,
        guardrails=[
            Guardrail(
                name="push_err",
                metric_source="push",
                metric="checkout",
                comparison=">",
                threshold=5.0,
                min_samples=30,
                window_seconds=60,
            )
        ],
    )
    client_mock = MagicMock()
    client_mock.set_percentage_rollout.return_value = {"status": "ok"}
    client_mock.get_percentage_rollout.return_value = 0.0
    provider = CompositeMetricProvider()
    provider.register_source("push", PushMetricSource())
    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=client_mock,
        audit_logger=AuditLogger(storage=storage),
        metric_provider=provider,
        storage=storage,
    )
    return create_app(engine, api_token="test-op-token")


def test_app_portal_and_root_link(tmp_path):
    app = _make_test_app(tmp_path)
    c = TestClient(app)
    r_root = c.get("/")
    assert r_root.status_code == 200
    assert "/app" in r_root.text

    r_app = c.get("/app")
    assert r_app.status_code == 200
    assert "Multi-User Workspaces" in r_app.text
    assert "localStorage" not in r_app.text


def test_user_signup_login_and_csrf_protection(tmp_path):
    app = _make_test_app(tmp_path)
    c = TestClient(app)

    # Weak password rejected
    r_bad = c.post("/api/auth/signup", json={"email": "a@example.com", "password": "short"})
    assert r_bad.status_code == 400

    # Valid signup
    r_up = c.post("/api/auth/signup", json={"email": "Alice@Example.com", "password": "Password123!", "name": "Alice"})
    assert r_up.status_code == 201
    data = r_up.json()
    assert data["user"]["email"] == "alice@example.com"
    csrf = data["csrf_token"]
    assert csrf

    # Duplicate signup rejected
    r_dup = c.post("/api/auth/signup", json={"email": "alice@example.com", "password": "Password123!"})
    assert r_dup.status_code == 400

    # /api/auth/me works with cookie
    r_me = c.get("/api/auth/me")
    assert r_me.status_code == 200
    assert r_me.json()["user"]["email"] == "alice@example.com"

    # Creating workspace without CSRF fails 403
    ws_payload = {
        "name": "Alice Checkout",
        "flagsmith_url": "https://app.flagsmith.com/api/v1",
        "flagsmith_admin_api_key": "ser.secret_admin_key_1234",
        "flagsmith_environment_key": "env_alice",
        "flag_name": "new_checkout",
    }
    r_no_csrf = c.post("/api/workspaces", json=ws_payload)
    assert r_no_csrf.status_code == 403

    # Creating workspace with CSRF succeeds and masks secret
    r_ws = c.post("/api/workspaces", json=ws_payload, headers={"X-CSRF-Token": csrf})
    assert r_ws.status_code == 201
    ws = r_ws.json()["workspace"]
    assert ws["flagsmith_admin_api_key_masked"] == "****1234"
    assert "ser.secret_admin_key_1234" not in r_ws.text
    assert ws["ingest_api_key"].startswith("grd_ws_")

    # Logout clears session
    r_out = c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert r_out.status_code == 200
    assert c.get("/api/auth/me").status_code == 401

    # Wrong password rejected, valid login succeeds
    assert c.post("/api/auth/login", json={"email": "alice@example.com", "password": "wrong"}).status_code == 401
    r_in = c.post("/api/auth/login", json={"email": "alice@example.com", "password": "Password123!"}).json()
    assert r_in["user"]["email"] == "alice@example.com"


def test_cross_tenant_isolation_and_workspace_ingest(tmp_path):
    app = _make_test_app(tmp_path)
    c_alice = TestClient(app)
    c_bob = TestClient(app)

    alice_csrf = c_alice.post(
        "/api/auth/signup",
        json={"email": "alice@example.com", "password": "Password123!"},
    ).json()["csrf_token"]
    bob_csrf = c_bob.post(
        "/api/auth/signup",
        json={"email": "bob@example.com", "password": "Password123!"},
    ).json()["csrf_token"]

    ws = c_alice.post(
        "/api/workspaces",
        json={
            "name": "Alice Prod",
            "flagsmith_url": "https://app.flagsmith.com/api/v1",
            "flagsmith_admin_api_key": "ser.alice_secret",
            "flagsmith_environment_key": "env_alice",
            "flag_name": "new_checkout",
        },
        headers={"X-CSRF-Token": alice_csrf},
    ).json()["workspace"]
    ws_id = ws["id"]
    ingest_key = ws["ingest_api_key"]

    # Bob cannot see or control Alice's workspace
    assert c_bob.get("/api/workspaces").json()["workspaces"] == []
    assert c_bob.get(f"/api/workspaces/{ws_id}/status").status_code == 404
    assert c_bob.post(f"/api/workspaces/{ws_id}/pause", json={}, headers={"X-CSRF-Token": bob_csrf}).status_code == 404
    assert c_bob.delete(f"/api/workspaces/{ws_id}", headers={"X-CSRF-Token": bob_csrf}).status_code == 404

    # Ingest rejects invalid Bearer key
    assert (
        c_bob.post(
            f"/api/workspaces/{ws_id}/metrics/ingest",
            json={"metric": "checkout", "requests": 50, "errors": 1},
            headers={"Authorization": "Bearer wrong-key"},
        ).status_code
        == 401
    )

    # Ingest accepts valid workspace ingest key
    r_ing = c_bob.post(
        f"/api/workspaces/{ws_id}/metrics/ingest",
        json={"metric": "checkout", "requests": 50, "errors": 1},
        headers={"Authorization": f"Bearer {ingest_key}"},
    )
    assert r_ing.status_code == 200

    # Alice can verify her workspace audit chain and rotate ingest key
    r_verify = c_alice.get(f"/api/workspaces/{ws_id}/audit/verify")
    assert r_verify.status_code == 200
    assert r_verify.json()["valid"] is True

    new_key = c_alice.post(
        f"/api/workspaces/{ws_id}/rotate-key",
        json={},
        headers={"X-CSRF-Token": alice_csrf},
    ).json()["ingest_api_key"]
    assert new_key != ingest_key

    # Old key no longer works
    assert (
        c_bob.post(
            f"/api/workspaces/{ws_id}/metrics/ingest",
            json={"metric": "checkout", "requests": 50, "errors": 1},
            headers={"Authorization": f"Bearer {ingest_key}"},
        ).status_code
        == 401
    )
