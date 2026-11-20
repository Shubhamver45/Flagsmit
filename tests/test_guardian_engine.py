import pytest
from guardian.config import Guardrail, RolloutConfig
from guardian.audit_log import AuditLogger
from guardian.engine import GuardianEngine
from guardian.state_machine import RolloutDecision, RolloutState


@pytest.fixture
def engine(fake_flagsmith):
    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1, 5, 25, 100],
        soak_time_seconds=10,
        require_approval_before_final=True,
        guardrails=[
            Guardrail(name="error_rate", metric_source="mock", comparison=">", threshold=5.0)
        ],
    )
    logger = AuditLogger()
    return GuardianEngine(
        config=cfg,
        flagsmith_client=fake_flagsmith,
        audit_logger=logger,
    )


def test_engine_lifecycle_advancement_and_approval(engine, fake_flagsmith):
    # Tick 1: PENDING -> STAGE_1 (1%)
    entry1 = engine.tick(metric_values={"error_rate": 0.0}, now=100.0)
    assert engine.current_state == "STAGE_1"
    assert engine.current_percentage == 1.0
    assert fake_flagsmith.current_percentage == 1.0
    assert fake_flagsmith.is_enabled is True
    assert entry1.decision == "ADVANCE"
    assert entry1.numbers["target_percentage"] == 1

    # Tick 2: 5s later -> HOLD (soaking)
    entry2 = engine.tick(metric_values={"error_rate": 1.0}, now=105.0)
    assert engine.current_state == "STAGE_1"
    assert entry2.decision == "HOLD"
    assert fake_flagsmith.current_percentage == 1.0

    # Tick 3: 12s later -> ADVANCE to STAGE_2 (5%)
    entry3 = engine.tick(metric_values={"error_rate": 0.5}, now=112.0)
    assert engine.current_state == "STAGE_2"
    assert engine.current_percentage == 5.0
    assert fake_flagsmith.current_percentage == 5.0
    assert entry3.decision == "ADVANCE"

    # Tick 4: 12s later -> ADVANCE to STAGE_3 (25%)
    entry4 = engine.tick(metric_values={"error_rate": 0.5}, now=124.0)
    assert engine.current_state == "STAGE_3"
    assert engine.current_percentage == 25.0
    assert fake_flagsmith.current_percentage == 25.0

    # Tick 5: 12s later -> AWAITING_APPROVAL before 100%
    entry5 = engine.tick(metric_values={"error_rate": 0.5}, now=136.0)
    assert engine.current_state == RolloutState.AWAITING_APPROVAL.value
    assert entry5.decision == "AWAIT_APPROVAL"
    # Flag remains at 25% while awaiting approval
    assert fake_flagsmith.current_percentage == 25.0

    # Tick 6: Operator approves -> PROMOTED (100%)
    entry6 = engine.approve(now=140.0)
    assert engine.current_state == RolloutState.PROMOTED.value
    assert engine.current_percentage == 100.0
    assert fake_flagsmith.current_percentage == 100.0
    assert entry6.decision == "PROMOTED"

    # Verify audit log recorded every step
    entries = engine.audit_logger.entries
    assert len(entries) == 6
    assert [e.decision for e in entries] == ["ADVANCE", "HOLD", "ADVANCE", "ADVANCE", "AWAIT_APPROVAL", "PROMOTED"]


def test_engine_instant_rollback_on_breach(engine, fake_flagsmith):
    # Start at Stage 1
    engine.tick(metric_values={"error_rate": 0.0}, now=100.0)
    assert fake_flagsmith.current_percentage == 1.0

    # Breach on next evaluation (error_rate = 8.5% > 5.0%)
    entry = engine.tick(metric_values={"error_rate": 8.5}, now=104.0)
    assert engine.current_state == RolloutState.ROLLED_BACK.value
    assert entry.decision == "ROLLBACK"
    assert fake_flagsmith.current_percentage == 0.0
    assert fake_flagsmith.is_enabled is False
    assert len(fake_flagsmith.rollback_calls) == 1
    assert entry.numbers["metric_value"] == 8.5
    assert entry.numbers["threshold"] == 5.0


def test_engine_manual_rollback(engine, fake_flagsmith):
    engine.tick(metric_values={"error_rate": 0.0}, now=100.0)
    assert fake_flagsmith.current_percentage == 1.0

    entry = engine.manual_rollback(reason="Critical Sentry alert")
    assert engine.current_state == RolloutState.ROLLED_BACK.value
    assert entry.decision == "ROLLBACK"
    assert fake_flagsmith.current_percentage == 0.0
    assert fake_flagsmith.is_enabled is False
    assert "Critical Sentry alert" in entry.reason
