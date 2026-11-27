"""Tests for Incident Intelligence model, SQLite persistence, and engine rollback wiring."""

import time
from unittest.mock import MagicMock

from guardian.config import RolloutConfig, Guardrail
from guardian.incident import Incident
from guardian.storage import GuardianStorage
from guardian.engine import GuardianEngine


def test_incident_dataclass_and_storage(tmp_path):
    storage = GuardianStorage(db_path=tmp_path / "inc.db")
    t0 = time.time()
    inc = Incident(
        flag_name="new_checkout",
        trigger_type="GUARDRAIL_BREACH",
        action_taken="ROLLBACK",
        explanation="Error rate spiked to 12.5% (> 5.0%)",
        metric_name="error_rate",
        observed_value=12.5,
        threshold=5.0,
        state_at_trigger="STAGE_2",
        percentage_at_trigger=5.0,
        triggered_at=t0,
    )
    assert not inc.is_resolved
    assert inc.containment_duration_seconds is None

    inc_id = storage.save_incident(inc)
    assert inc_id is not None and inc.id == inc_id

    storage.resolve_incident(inc_id, now=t0 + 4.5)
    rows = storage.get_incidents(flag_name="new_checkout", limit=10)
    assert len(rows) == 1
    assert rows[0]["flag_name"] == "new_checkout"
    assert rows[0]["trigger_type"] == "GUARDRAIL_BREACH"
    assert rows[0]["is_resolved"] is True
    assert rows[0]["containment_duration_seconds"] == 4.5
    storage.close()


def test_engine_records_incident_on_rollback(tmp_path):
    storage = GuardianStorage(db_path=tmp_path / "eng_inc.db")
    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0

    cfg = RolloutConfig(
        flag_name="checkout_v2",
        stages=[1.0, 5.0, 100.0],
        soak_time_seconds=0,
        guardrails=[
            Guardrail(
                name="error_rate",
                metric_source="prometheus",
                query="rate(errors[1m])",
                threshold=2.0,
                comparison=">",
            )
        ],
    )

    metrics = {"error_rate": 0.1}
    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=mock_client,
        metric_provider=lambda: metrics,
        storage=storage,
        skip_startup_reconciliation=True,
    )

    # Tick 1: PENDING -> STAGE_1 (1%)
    engine.tick()
    assert engine.state_machine.current_state == "STAGE_1"

    # Inject breach and tick
    metrics["error_rate"] = 9.5
    entry = engine.tick()
    assert entry.decision == "ROLLBACK"

    incidents = storage.get_incidents(flag_name="checkout_v2")
    assert len(incidents) == 1
    assert incidents[0]["action_taken"] == "ROLLBACK"
    assert incidents[0]["metric_name"] == "error_rate"
    assert incidents[0]["observed_value"] == 9.5
    assert "9.5" in incidents[0]["explanation"]
    storage.close()
