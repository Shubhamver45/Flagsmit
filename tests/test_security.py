"""
Security and Hardening Verification Test Suite.
Verifies production mode enforcement, rate limiting, request size limits,
secret redaction in logs, token-in-URL prevention, and fail-closed webhooks.
"""

import io
import json
import logging
import os
import pytest
from fastapi.testclient import TestClient

from guardian.api import create_app, InMemoryRateLimiter, write_rate_limiter
from guardian.config import RolloutConfig
from guardian.engine import GuardianEngine
from guardian.main import check_production_safety, SecretRedactingFilter
from guardian.storage import GuardianStorage
from guardian.audit_log import AuditLogger
from guardian.flagsmith_client import FlagsmithAdminClient
from guardian.metrics.registry import CompositeMetricProvider


class DummyFlagsmithClient:
    def __init__(self):
        self.pct = 25.0

    def get_percentage_rollout(self, flag_name: str) -> float:
        return self.pct

    def set_percentage_rollout(self, flag_name: str, percentage: float) -> dict:
        self.pct = percentage
        return {"status": "ok"}

    def rollback_to_zero(self, flag_name: str, reason: str = "") -> dict:
        self.pct = 0.0
        return {"status": "ok"}


@pytest.fixture
def test_engine(tmp_path):
    db_file = str(tmp_path / "test_sec.db")
    storage = GuardianStorage(db_path=db_file)
    cfg = RolloutConfig(flag_name="sec_flag", stages=[1, 5, 25, 100], soak_time_seconds=1)
    logger = AuditLogger(storage=storage)
    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=DummyFlagsmithClient(),
        audit_logger=logger,
        metric_provider=CompositeMetricProvider(),
        storage=storage,
    )
    # Put rollout into AWAITING_APPROVAL
    engine.state_machines["sec_flag"].restore_state("AWAITING_APPROVAL", 2, stage_entered_at=1000.0)
    return engine


def test_production_mode_refuses_startup_without_token(monkeypatch):
    """Refuses to start when GUARDIAN_ENV=production and GUARDIAN_API_TOKEN is unset."""
    monkeypatch.setenv("GUARDIAN_ENV", "production")
    monkeypatch.delenv("GUARDIAN_API_TOKEN", raising=False)
    monkeypatch.delenv("GUARDIAN_TOKEN", raising=False)

    with pytest.raises(SystemExit) as exc:
        check_production_safety()
    assert exc.value.code == 1


def test_production_mode_allows_startup_with_token(monkeypatch):
    """Permits startup when GUARDIAN_ENV=production and GUARDIAN_API_TOKEN is set."""
    monkeypatch.setenv("GUARDIAN_ENV", "production")
    monkeypatch.setenv("GUARDIAN_API_TOKEN", "prod-secret-token-12345")

    # Should not raise SystemExit
    check_production_safety()


def test_production_webhook_fails_closed_without_secret(monkeypatch, test_engine):
    """In production mode, webhooks are rejected (fail-closed) if no secret is configured."""
    monkeypatch.setenv("GUARDIAN_ENV", "production")
    monkeypatch.delenv("FLAGSMITH_WEBHOOK_SECRET", raising=False)

    app = create_app(test_engine)
    client = TestClient(app)

    resp = client.post("/webhooks/flagsmith", json={"event_type": "FLAG_UPDATED"})
    assert resp.status_code == 401
    assert "FLAGSMITH_WEBHOOK_SECRET is not configured in production mode" in resp.json()["detail"]


def test_token_in_url_query_parameter_strictly_forbidden(monkeypatch, test_engine):
    """Tokens passed in URL query parameters are rejected with 401 Unauthorized."""
    monkeypatch.setenv("GUARDIAN_API_TOKEN", "super-secret-token")
    app = create_app(test_engine)
    client = TestClient(app)

    # Passing token in query parameter must be rejected
    resp = client.post("/approve?token=super-secret-token")
    assert resp.status_code == 401
    assert "Tokens in URL query parameters are forbidden" in resp.json()["detail"]

    # Passing api_token in query parameter must be rejected
    resp = client.post("/approve?api_token=super-secret-token")
    assert resp.status_code == 401
    assert "Tokens in URL query parameters are forbidden" in resp.json()["detail"]


def test_request_size_limit_enforced(test_engine):
    """Request payloads exceeding 64KB are rejected with 413 Payload Too Large."""
    app = create_app(test_engine)
    client = TestClient(app)

    # Oversized payload (> 64KB)
    large_payload = {"padding": "A" * 70000}
    resp = client.post("/approve", json=large_payload)
    assert resp.status_code == 413
    assert "Request entity too large" in resp.json()["detail"]


def test_rate_limiting_on_write_endpoints(monkeypatch, test_engine):
    """Write endpoints enforce rate limiting per client IP."""
    monkeypatch.setenv("GUARDIAN_RATE_LIMIT_PER_MINUTE", "5")
    # Reset limiter requests
    write_rate_limiter.requests.clear()

    app = create_app(test_engine)
    client = TestClient(app)

    # First 5 calls pass
    for i in range(5):
        resp = client.post("/tick")
        assert resp.status_code == 200

    # 6th call exceeds rate limit
    resp = client.post("/tick")
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == "60"
    assert "Rate limit exceeded" in resp.json()["detail"]


def test_secret_redacting_logger_never_leaks_secrets(monkeypatch, test_engine):
    """Verifies that configured secrets never appear in captured log records."""
    token = "secret-token-abcdef-987654"
    webhook_secret = "webhook-hmac-secret-xyz123"
    admin_key = "flagsmith-master-key-99999"

    monkeypatch.setenv("GUARDIAN_API_TOKEN", token)
    monkeypatch.setenv("FLAGSMITH_WEBHOOK_SECRET", webhook_secret)
    monkeypatch.setenv("FLAGSMITH_ADMIN_API_KEY", admin_key)

    # Setup redacting logger with StringIO stream handler
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.addFilter(SecretRedactingFilter())
    test_logger = logging.getLogger("test_security_audit")
    test_logger.setLevel(logging.INFO)
    test_logger.addHandler(handler)

    # Simulate log messages that might inadvertently reference secrets
    test_logger.info(f"Connecting with token={token} and admin_key={admin_key}")
    test_logger.warning(f"Webhook failed with secret={webhook_secret}")
    handler.flush()

    captured = log_stream.getvalue()
    # Confirm secrets are scrubbed
    assert token not in captured
    assert webhook_secret not in captured
    assert admin_key not in captured
    assert "[REDACTED]" in captured
