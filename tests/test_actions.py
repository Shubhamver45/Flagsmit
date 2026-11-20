import pytest
from unittest.mock import MagicMock
from pathlib import Path
import tempfile

from guardian.actions.alerts import AlertDispatcher
from guardian.actions.idempotency import IdempotencyManager
from guardian.audit_log import AuditEntry
from guardian.config import Guardrail, RolloutConfig
from guardian.engine import GuardianEngine


def test_alert_dispatcher_slack_and_generic():
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_session.post.return_value = mock_resp

    dispatcher = AlertDispatcher(
        slack_webhook_url="https://hooks.slack.com/services/test",
        generic_webhook_url="https://example.com/webhook",
        session=mock_session,
    )

    entry = AuditEntry(
        timestamp="2026-11-20T12:00:00Z",
        flag_name="new_checkout",
        from_state="STAGE_1",
        to_state="ROLLED_BACK",
        decision="ROLLBACK",
        reason="Error rate 28.9% > threshold 5.0%",
        numbers={"metric_value": 28.9, "threshold": 5.0},
    )

    results = dispatcher.dispatch(entry)
    assert results["slack"] is True
    assert results["generic"] is True
    assert mock_session.post.call_count == 2

    # Verify Slack payload structure
    slack_call_args = mock_session.post.call_args_list[0]
    slack_payload = slack_call_args.kwargs["json"]
    assert "blocks" in slack_payload
    assert "🚨 Guardian Alert: ROLLBACK" in slack_payload["text"]


def test_idempotency_manager_deduplication():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        temp_path = tf.name

    try:
        manager = IdempotencyManager(persistence_file=temp_path)

        # First execution -> should execute
        assert manager.should_execute(
            flag_name="new_checkout",
            decision="ROLLBACK",
            to_state="ROLLED_BACK",
            stage_index=0,
        ) is True

        manager.mark_executed(
            flag_name="new_checkout",
            decision="ROLLBACK",
            to_state="ROLLED_BACK",
            stage_index=0,
        )

        # Duplicate execution -> should NOT execute
        assert manager.should_execute(
            flag_name="new_checkout",
            decision="ROLLBACK",
            to_state="ROLLED_BACK",
            stage_index=0,
        ) is False

        # Simulate service restart with new instance pointing to same persistence file
        restarted_manager = IdempotencyManager(persistence_file=temp_path)
        assert restarted_manager.should_execute(
            flag_name="new_checkout",
            decision="ROLLBACK",
            to_state="ROLLED_BACK",
            stage_index=0,
        ) is False

    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_engine_idempotent_actions_no_double_fire(fake_flagsmith):
    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1, 5, 25, 100],
        soak_time_seconds=10,
        guardrails=[
            Guardrail(name="err", metric_source="mock", comparison=">", threshold=5.0)
        ],
    )

    mock_alert_dispatcher = MagicMock()
    idempotency = IdempotencyManager()

    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=fake_flagsmith,
        alert_dispatcher=mock_alert_dispatcher,
        idempotency_manager=idempotency,
    )

    # Initial start
    engine.tick(metric_values={"err": 0.0}, now=100.0)
    assert fake_flagsmith.current_percentage == 1.0
    assert mock_alert_dispatcher.dispatch.call_count == 1  # ADVANCE dispatched

    # Breach occurs
    engine.tick(metric_values={"err": 15.0}, now=102.0)
    assert engine.current_state == "ROLLED_BACK"
    assert len(fake_flagsmith.rollback_calls) == 1
    assert mock_alert_dispatcher.dispatch.call_count == 2  # ROLLBACK dispatched

    # Consecutive ticks while already ROLLED_BACK (e.g. repeated loop ticks or restarts)
    engine.tick(metric_values={"err": 15.0}, now=104.0)
    engine.tick(metric_values={"err": 15.0}, now=106.0)

    # Verify rollback and alert dispatch were NOT called again!
    assert len(fake_flagsmith.rollback_calls) == 1
    assert mock_alert_dispatcher.dispatch.call_count == 2
