import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from guardian.actions.alerts import AlertDispatcher
from guardian.api import create_app
from guardian.audit_log import AuditEntry
from guardian.config import Guardrail, GuardianConfig, RolloutConfig
from guardian.metrics.cloudwatch import CloudWatchMetricSource
from guardian.metrics.datadog import DatadogMetricSource
from guardian.metrics.base import MetricStatus
from guardian.engine import GuardianEngine


def _make_guardrail(source: str, query: str, threshold: float = 5.0, min_samples: int = 10):
    return Guardrail(
        name=f"test_{source}",
        metric_source=source,
        comparison=">",
        threshold=threshold,
        query=query,
        min_samples=min_samples,
    )


def test_datadog_healthy_and_breach():
    sess = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {"series": [{"pointlist": [[1700000000, 1.2], [1700000060, 2.5]]}]}
    sess.get.return_value = resp

    src = DatadogMetricSource(api_key="dd-api", app_key="dd-app", session=sess)
    g = _make_guardrail("datadog", "avg:checkout.error_rate{*}")
    ev = src.evaluate(g)
    assert ev.status == MetricStatus.HEALTHY
    assert ev.is_breached is False
    assert ev.value == 2.5

    resp.json.return_value = {"series": [{"pointlist": [[1700000060, 8.4]]}]}
    ev_breach = src.evaluate(g)
    assert ev_breach.status == MetricStatus.BREACH
    assert ev_breach.is_breached is True
    assert ev_breach.value == 8.4


def test_datadog_missing_keys_and_empty_series():
    src_no_keys = DatadogMetricSource(api_key="", app_key="")
    ev = src_no_keys.evaluate(_make_guardrail("datadog", "avg:foo{*}"))
    assert ev.status == MetricStatus.ERROR

    sess = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {"series": []}
    sess.get.return_value = resp
    src = DatadogMetricSource(api_key="k", app_key="a", session=sess)
    ev2 = src.evaluate(_make_guardrail("datadog", "avg:foo{*}"))
    assert ev2.status == MetricStatus.INSUFFICIENT_DATA


def test_cloudwatch_healthy_breach_and_insufficient():
    cw = MagicMock()
    cw.get_metric_statistics.return_value = {
        "Datapoints": [
            {"Timestamp": datetime.now(timezone.utc), "Average": 1.5, "SampleCount": 40.0}
        ]
    }
    src = CloudWatchMetricSource(client=cw)
    g = _make_guardrail("cloudwatch", "AWS/ApplicationELB/HTTPCode_Target_5XX_Count", threshold=5.0, min_samples=20)
    ev = src.evaluate(g)
    assert ev.status == MetricStatus.HEALTHY
    assert ev.value == 1.5
    assert ev.sample_size == 40

    cw.get_metric_statistics.return_value = {
        "Datapoints": [
            {"Timestamp": datetime.now(timezone.utc), "Average": 7.2, "SampleCount": 50.0}
        ]
    }
    ev_b = src.evaluate(g)
    assert ev_b.status == MetricStatus.BREACH
    assert ev_b.is_breached is True

    cw.get_metric_statistics.return_value = {
        "Datapoints": [
            {"Timestamp": datetime.now(timezone.utc), "Average": 7.2, "SampleCount": 5.0}
        ]
    }
    ev_low = src.evaluate(g)
    assert ev_low.status == MetricStatus.INSUFFICIENT_DATA


def test_slack_alert_includes_approval_buttons():
    sess = MagicMock()
    resp = MagicMock(status_code=200)
    sess.post.return_value = resp
    dispatcher = AlertDispatcher(slack_webhook_url="https://hooks.slack.com/services/T/B/X", session=sess)
    entry = AuditEntry(
        timestamp="2026-11-28T00:00:00Z",
        flag_name="new_checkout",
        from_state="STAGE_3",
        to_state="AWAITING_APPROVAL",
        decision="AWAIT_APPROVAL",
        reason="Ready for human approval",
        numbers={"current_percentage": 25},
    )
    dispatcher.dispatch(entry)
    sent_payload = sess.post.call_args.kwargs["json"]
    actions_blocks = [b for b in sent_payload["blocks"] if b.get("type") == "actions"]
    assert len(actions_blocks) == 1
    action_ids = [el["action_id"] for el in actions_blocks[0]["elements"]]
    assert action_ids == ["guardian_approve", "guardian_rollback"]


def test_slack_interactive_webhook_signature_and_actions(monkeypatch, tmp_path):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret-123")
    cfg = GuardianConfig(
        rollouts=[RolloutConfig(flag_name="new_checkout", stages=[1, 5, 25, 100])],
        db_path=str(tmp_path / "guardian.db"),
    )
    fs = MagicMock()
    fs.get_percentage_rollout.return_value = 0.0
    mp = MagicMock()
    mp.evaluate_all.return_value = []
    engine = GuardianEngine(config=cfg, flagsmith_client=fs, metric_provider=mp)
    engine.approve = MagicMock(return_value={"flag_name": "new_checkout", "state": "PROMOTED"})
    engine.manual_rollback = MagicMock(return_value={"flag_name": "new_checkout", "state": "ROLLED_BACK"})

    app = create_app(engine)
    client = TestClient(app)

    ts = str(int(time.time()))
    payload_str = json.dumps({"actions": [{"action_id": "guardian_approve", "value": "new_checkout"}], "user": {"username": "alice"}})
    body = urlencode({"payload": payload_str}).encode("utf-8")
    sig = "v0=" + hmac.new(b"slack-secret-123", f"v0:{ts}:".encode("utf-8") + body, hashlib.sha256).hexdigest()

    # Invalid signature -> 401
    bad = client.post("/webhooks/slack", content=body, headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": "v0=bad"})
    assert bad.status_code == 401

    # Valid approve -> calls engine.approve
    ok = client.post("/webhooks/slack", content=body, headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig})
    assert ok.status_code == 200
    assert "Approved 100%" in ok.json()["text"]
    engine.approve.assert_called_once()

    # Valid rollback -> calls engine.manual_rollback
    rb_payload = json.dumps({"actions": [{"action_id": "guardian_rollback", "value": "new_checkout"}], "user": {"username": "alice"}})
    rb_body = urlencode({"payload": rb_payload}).encode("utf-8")
    rb_sig = "v0=" + hmac.new(b"slack-secret-123", f"v0:{ts}:".encode("utf-8") + rb_body, hashlib.sha256).hexdigest()
    rb_res = client.post("/webhooks/slack", content=rb_body, headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": rb_sig})
    assert rb_res.status_code == 200
    assert "Rolled back" in rb_res.json()["text"]
    engine.manual_rollback.assert_called_once()
