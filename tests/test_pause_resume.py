"""Tests for PAUSED state, pause(), and resume() in RolloutStateMachine and GuardianEngine."""

import pytest
from unittest.mock import MagicMock

from guardian.config import RolloutConfig, Guardrail
from guardian.state_machine import RolloutStateMachine, RolloutState, RolloutDecision
from guardian.engine import GuardianEngine
from guardian.storage import GuardianStorage


def _make_config():
    return RolloutConfig(
        flag_name="test_flag",
        stages=[1.0, 5.0, 25.0, 100.0],
        soak_time_seconds=10,
        require_approval_before_final=True,
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


def test_state_machine_pause_and_resume():
    sm = RolloutStateMachine(_make_config())
    # Cannot pause PENDING
    with pytest.raises(ValueError, match="Cannot pause"):
        sm.pause()

    # Start -> STAGE_1 (1.0%)
    sm.start(now=100.0)
    assert sm.current_state == "STAGE_1"
    assert sm.current_percentage == 1.0

    # Pause -> PAUSED (preserves 1.0%)
    state, decision, reason, nums = sm.pause()
    assert state == RolloutState.PAUSED.value
    assert decision == RolloutDecision.HOLD
    assert sm.current_percentage == 1.0
    assert nums["paused_from_state"] == "STAGE_1"

    # Cannot pause twice
    with pytest.raises(ValueError, match="Cannot pause"):
        sm.pause()

    # Evaluate while PAUSED holds without advancing even after soak time
    eval_state, eval_dec, eval_reason, _ = sm.evaluate(metric_values={"error_rate": 0.5}, now=200.0)
    assert eval_state == RolloutState.PAUSED.value
    assert eval_dec == RolloutDecision.HOLD
    assert "paused" in eval_reason.lower()

    # Resume -> STAGE_1 (1.0%)
    res_state, res_dec, res_reason, _ = sm.resume()
    assert res_state == "STAGE_1"
    assert res_dec == RolloutDecision.ADVANCE
    assert sm.current_percentage == 1.0

    # Cannot resume when not paused
    with pytest.raises(ValueError, match="Cannot resume"):
        sm.resume()


def test_state_machine_breach_while_paused_still_rolls_back():
    sm = RolloutStateMachine(_make_config())
    sm.start(now=100.0)
    sm.pause()
    state, decision, _, _ = sm.evaluate(metric_values={"error_rate": 10.0}, now=110.0)
    assert state == RolloutState.ROLLED_BACK.value
    assert decision == RolloutDecision.ROLLBACK


def test_engine_pause_and_resume_persists(tmp_path):
    db_path = tmp_path / "test_pause.db"
    storage = GuardianStorage(db_path=db_path)
    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0

    engine = GuardianEngine(
        config=_make_config(),
        flagsmith_client=mock_client,
        metric_provider=lambda: {"error_rate": 0.1},
        storage=storage,
        skip_startup_reconciliation=True,
    )

    # First tick initiates STAGE_1
    engine.tick()
    assert engine.state_machine.current_state == "STAGE_1"

    # Pause
    entry = engine.pause()
    assert entry.to_state == "PAUSED"
    saved = storage.load_rollout_state("test_flag")
    assert saved["current_state"] == "PAUSED"
    assert saved["current_percentage"] == 1.0

    # Resume
    entry2 = engine.resume()
    assert entry2.to_state == "STAGE_1"
    saved2 = storage.load_rollout_state("test_flag")
    assert saved2["current_state"] == "STAGE_1"
    storage.close()
