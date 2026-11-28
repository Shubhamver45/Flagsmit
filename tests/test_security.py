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


def test_production_status_sanitizes_prometheus_error_for_unauthenticated(tmp_path, monkeypatch, caplog):
    """In GUARDIAN_ENV=production, unauthenticated /status must not leak hostnames, ports, or PromQL queries."""
    import logging
    from unittest.mock import MagicMock
    from fastapi.testclient import TestClient
    import requests as _requests

    from guardian.api import create_app
    from guardian.config import RolloutConfig, Guardrail
    from guardian.engine import GuardianEngine
    from guardian.metrics.prometheus import PrometheusMetricSource
    from guardian.metrics.registry import CompositeMetricProvider
    from guardian.storage import GuardianStorage

    monkeypatch.setenv("GUARDIAN_ENV", "production")
    monkeypatch.setenv("GUARDIAN_API_TOKEN", "prod-secret-token")

    bad_session = MagicMock()
    bad_session.get.side_effect = _requests.exceptions.ConnectionError(
        "HTTPConnectionPool(host='prom-internal.corp', port=9090): Max retries exceeded with url: /api/v1/query?query=rate(http_errors_total[5m])"
    )
    prom = PrometheusMetricSource("http://prom-internal.corp:9090", session=bad_session)
    provider = CompositeMetricProvider()
    provider.register_source("prometheus", prom)

    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1.0, 25.0, 100.0],
        soak_time_seconds=0,
        require_approval_before_final=True,
        guardrails=[
            Guardrail(
                name="new_flow_error_rate",
                metric_source="prometheus",
                comparison=">",
                threshold=5.0,
                query="rate(http_errors_total[5m])",
            )
        ],
    )
    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0
    storage = GuardianStorage(db_path=tmp_path / "prod_status.db")
    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=mock_client,
        metric_provider=provider,
        storage=storage,
        skip_startup_reconciliation=True,
    )

    with caplog.at_level(logging.ERROR):
        engine.tick()
    assert "prom-internal.corp" in caplog.text  # full error kept in server log

    app = create_app(engine=engine, api_token="prod-secret-token")
    client = TestClient(app)

    # Unauthenticated /status must NOT contain hostname, port, or query string
    public_resp = client.get("/status")
    assert public_resp.status_code == 200
    public_text = public_resp.text
    assert "prom-internal.corp" not in public_text
    assert "9090" not in public_text
    assert "http_errors_total" not in public_text
    assert "metrics source unreachable" in public_text

    # Authenticated /status retains full diagnostic detail
    auth_resp = client.get("/status", headers={"Authorization": "Bearer prod-secret-token"})
    assert auth_resp.status_code == 200
    assert "prom-internal.corp" in auth_resp.text
    storage.close()


def test_production_unsigned_webhook_post_rejected(tmp_path, monkeypatch):
    """Unsigned POST /webhooks/flagsmith must be rejected (never 200) in production mode."""
    from unittest.mock import MagicMock
    from fastapi.testclient import TestClient

    from guardian.api import create_app
    from guardian.config import RolloutConfig
    from guardian.engine import GuardianEngine
    from guardian.storage import GuardianStorage

    monkeypatch.setenv("GUARDIAN_ENV", "production")
    monkeypatch.setenv("FLAGSMITH_WEBHOOK_SECRET", "prod-webhook-secret")

    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0
    storage = GuardianStorage(db_path=tmp_path / "prod_wh.db")
    engine = GuardianEngine(
        config=RolloutConfig(flag_name="new_checkout", stages=[1, 100], soak_time_seconds=0),
        flagsmith_client=mock_client,
        storage=storage,
        skip_startup_reconciliation=True,
    )
    app = create_app(engine=engine, api_token="prod-tok")
    client = TestClient(app)

    resp = client.post("/webhooks/flagsmith", json={"event_type": "FLAG_UPDATED", "data": {}})
    assert resp.status_code == 401
    storage.close()


def test_chunked_oversized_request_without_content_length_returns_413(monkeypatch, tmp_path):
    """Requests without Content-Length (streaming/chunked) exceeding 64KB must be rejected with 413."""
    from unittest.mock import MagicMock
    from fastapi.testclient import TestClient
    from guardian.api import create_app
    from guardian.config import RolloutConfig
    from guardian.engine import GuardianEngine
    from guardian.storage import GuardianStorage

    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0
    storage = GuardianStorage(db_path=tmp_path / "chunked.db")
    engine = GuardianEngine(
        config=RolloutConfig(flag_name="new_checkout", stages=[1, 100], soak_time_seconds=0),
        flagsmith_client=mock_client,
        storage=storage,
        skip_startup_reconciliation=True,
    )
    app = create_app(engine=engine, api_token="tok-123")
    client = TestClient(app)

    def gen():
        yield b'{"metric":"checkout","pad":"'
        yield b"x" * 70000
        yield b'"}'

    resp = client.post(
        "/metrics/ingest",
        content=gen(),
        headers={"Authorization": "Bearer tok-123", "Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    storage.close()


def test_webhook_replay_and_audit_chain_tamper_detection(monkeypatch, tmp_path):
    """Signed webhook replay with X-Flagsmith-Timestamp is rejected (409) and audit tampering is detected."""
    import hashlib
    import hmac
    import json
    import time
    from unittest.mock import MagicMock
    from fastapi.testclient import TestClient
    from guardian.api import create_app
    from guardian.config import RolloutConfig
    from guardian.engine import GuardianEngine
    from guardian.storage import GuardianStorage

    monkeypatch.setenv("FLAGSMITH_WEBHOOK_SECRET", "wh-sec")
    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0
    storage = GuardianStorage(db_path=tmp_path / "audit_chain.db")
    engine = GuardianEngine(
        config=RolloutConfig(flag_name="new_checkout", stages=[1, 100], soak_time_seconds=0),
        flagsmith_client=mock_client,
        storage=storage,
        skip_startup_reconciliation=True,
    )
    app = create_app(engine=engine, api_token="tok-123")
    client = TestClient(app)

    raw = json.dumps({"event_type": "FLAG_UPDATED", "data": {}}).encode("utf-8")
    sig = hmac.new(b"wh-sec", raw, hashlib.sha256).hexdigest()
    ts = str(int(time.time()))

    r1 = client.post("/webhooks/flagsmith", content=raw, headers={"X-Flagsmith-Signature": sig, "X-Flagsmith-Timestamp": ts})
    assert r1.status_code == 200
    r2 = client.post("/webhooks/flagsmith", content=raw, headers={"X-Flagsmith-Signature": sig, "X-Flagsmith-Timestamp": ts})
    assert r2.status_code == 409

    # Audit hash chain verification
    storage.record_audit("2026-11-28T00:00:00Z", "new_checkout", "PENDING", "STAGE_1", "ADVANCE", "ok", {"pct": 1})
    storage.record_audit("2026-11-28T00:01:00Z", "new_checkout", "STAGE_1", "ROLLED_BACK", "ROLLBACK", "breach", {"err": 10})
    assert client.get("/audit/verify").json()["valid"] is True

    # Tamper with reason in SQLite directly
    with storage._lock, storage._conn:
        storage._conn.execute("UPDATE audit_log SET reason = 'tampered' WHERE decision = 'ROLLBACK'")
    res = client.get("/audit/verify").json()
    assert res["valid"] is False
    storage.close()


def test_dashboard_uses_httponly_session_and_csrf_not_localstorage(tmp_path):
    """Dashboard HTML must not use localStorage for tokens; /auth/session uses HttpOnly cookie + CSRF."""
    from unittest.mock import MagicMock
    from fastapi.testclient import TestClient
    from guardian.api import create_app
    from guardian.config import RolloutConfig
    from guardian.engine import GuardianEngine
    from guardian.storage import GuardianStorage
    from guardian.status_page import STATUS_PAGE_HTML

    assert "localStorage" not in STATUS_PAGE_HTML

    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0
    storage = GuardianStorage(db_path=tmp_path / "sess.db")
    engine = GuardianEngine(
        config=RolloutConfig(flag_name="new_checkout", stages=[1, 100], soak_time_seconds=0),
        flagsmith_client=mock_client,
        storage=storage,
        skip_startup_reconciliation=True,
    )
    app = create_app(engine=engine, api_token="super-secret-op")
    client = TestClient(app)

    # Wrong token rejected
    assert client.post("/auth/session", json={"token": "wrong"}).status_code == 401

    # Valid token sets HttpOnly guardian_session cookie & returns csrf_token
    login = client.post("/auth/session", json={"token": "super-secret-op"})
    assert login.status_code == 200
    assert "guardian_session" in login.cookies
    set_cookie_hdr = login.headers.get("set-cookie", "").lower()
    assert "httponly" in set_cookie_hdr
    assert "samesite=lax" in set_cookie_hdr
    csrf_tok = login.json()["csrf_token"]

    # State-changing POST with session cookie but WITHOUT X-CSRF-Token -> 403 CSRF blocked
    no_csrf = client.post("/tick")
    assert no_csrf.status_code == 403

    # With matching X-CSRF-Token -> 200
    with_csrf = client.post("/tick", headers={"X-CSRF-Token": csrf_tok})
    assert with_csrf.status_code == 200

    # Logout invalidates session
    client.delete("/auth/session")
    assert client.post("/tick", headers={"X-CSRF-Token": csrf_tok}).status_code == 401
    storage.close()
