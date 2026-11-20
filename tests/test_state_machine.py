import pytest
from guardian.config import Guardrail, RolloutConfig
from guardian.state_machine import RolloutDecision, RolloutState, RolloutStateMachine


@pytest.fixture
def standard_config():
    return RolloutConfig(
        flag_name="new_checkout",
        stages=[1, 5, 25, 100],
        soak_time_seconds=20,
        require_approval_before_final=True,
        guardrails=[
            Guardrail(name="error_rate", metric_source="mock", comparison=">", threshold=5.0)
        ],
    )


def test_initial_pending_to_stage_1(standard_config):
    sm = RolloutStateMachine(standard_config)
    assert sm.current_state == RolloutState.PENDING.value
    assert sm.current_percentage == 0.0

    state, decision, reason, numbers = sm.evaluate(metric_values={"error_rate": 0.0}, now=100.0)
    assert state == "STAGE_1"
    assert decision == RolloutDecision.ADVANCE
    assert sm.current_percentage == 1.0
    assert numbers["target_percentage"] == 1


def test_soak_time_hold(standard_config):
    sm = RolloutStateMachine(standard_config)
    sm.evaluate(metric_values={"error_rate": 0.0}, now=100.0)

    # 10 seconds later (soak time is 20s) -> should HOLD
    state, decision, reason, numbers = sm.evaluate(metric_values={"error_rate": 1.2}, now=110.0)
    assert state == "STAGE_1"
    assert decision == RolloutDecision.HOLD
    assert "Soaking" in reason
    assert numbers["soak_elapsed_seconds"] == 10.0


def test_soak_time_advance(standard_config):
    sm = RolloutStateMachine(standard_config)
    sm.evaluate(metric_values={"error_rate": 0.0}, now=100.0)

    # 25 seconds later (> 20s soak) -> should ADVANCE to STAGE_2 (5%)
    state, decision, reason, numbers = sm.evaluate(metric_values={"error_rate": 0.5}, now=125.0)
    assert state == "STAGE_2"
    assert decision == RolloutDecision.ADVANCE
    assert sm.current_percentage == 5.0
    assert numbers["target_percentage"] == 5

    # 25 seconds later -> should ADVANCE to STAGE_3 (25%)
    state, decision, reason, numbers = sm.evaluate(metric_values={"error_rate": 0.8}, now=150.0)
    assert state == "STAGE_3"
    assert decision == RolloutDecision.ADVANCE
    assert sm.current_percentage == 25.0


def test_guardrail_breach_triggers_rollback(standard_config):
    sm = RolloutStateMachine(standard_config)
    sm.evaluate(metric_values={"error_rate": 0.0}, now=100.0)  # STAGE_1 (1%)

    # Metric breaches threshold (error_rate 6.2 > 5.0)
    state, decision, reason, numbers = sm.evaluate(metric_values={"error_rate": 6.2}, now=105.0)
    assert state == RolloutState.ROLLED_BACK.value
    assert decision == RolloutDecision.ROLLBACK
    assert sm.current_percentage == 0.0
    assert "breached" in reason
    assert numbers["metric_value"] == 6.2
    assert numbers["threshold"] == 5.0


def test_awaiting_approval_and_promotion(standard_config):
    sm = RolloutStateMachine(standard_config)
    sm.evaluate(metric_values={"error_rate": 0.0}, now=100.0)  # STAGE_1 (1%)
    sm.evaluate(metric_values={"error_rate": 0.0}, now=125.0)  # STAGE_2 (5%)
    sm.evaluate(metric_values={"error_rate": 0.0}, now=150.0)  # STAGE_3 (25%)

    # After STAGE_3 finishes soak (now=175.0), next is 100% -> must AWAIT_APPROVAL
    state, decision, reason, numbers = sm.evaluate(metric_values={"error_rate": 0.2}, now=175.0)
    assert state == RolloutState.AWAITING_APPROVAL.value
    assert decision == RolloutDecision.AWAIT_APPROVAL
    assert "requires human approval" in reason

    # Further ticks without approval remain in AWAITING_APPROVAL
    state, decision, reason, numbers = sm.evaluate(metric_values={"error_rate": 0.2}, now=180.0)
    assert state == RolloutState.AWAITING_APPROVAL.value
    assert decision == RolloutDecision.HOLD

    # Operator grants approval
    sm.approve()
    state, decision, reason, numbers = sm.evaluate(metric_values={"error_rate": 0.2}, now=185.0)
    assert state == RolloutState.PROMOTED.value
    assert decision == RolloutDecision.PROMOTED
    assert sm.current_percentage == 100.0
