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


def test_engine_approval_blocked_on_collecting_data(fake_flagsmith):
    # Unit-test mock fixture: verifying approval blocked when Flagsmith is still collecting data
    from guardian.metrics.base import MetricEvaluation, MetricStatus
    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1, 5, 25, 100],
        soak_time_seconds=10,
        require_approval_before_final=True,
        guardrails=[
            Guardrail(name="checkout_ab", metric_source="flagsmith_experiment", comparison=">", threshold=10.0, experiment_id=1)
        ],
    )
    engine = GuardianEngine(config=cfg, flagsmith_client=fake_flagsmith)
    engine.state_machine.current_state = RolloutState.AWAITING_APPROVAL.value

    engine.latest_evaluations = [
        MetricEvaluation(
            name="checkout_ab",
            value=None,
            sample_size=12,
            is_breached=False,
            status=MetricStatus.INSUFFICIENT_DATA,
            reason="Flagsmith is collecting data (under 50 identities per arm)",
            details={"as_of": "2026-11-22T13:22:48Z", "is_collecting_data": True, "srm_warning": False},
        )
    ]

    with pytest.raises(ValueError, match="Flagsmith is still collecting data"):
        engine.approve()


def test_engine_approval_blocked_on_srm_warning(fake_flagsmith):
    # Unit-test mock fixture: verifying approval blocked when real SRM warning detected
    from guardian.metrics.base import MetricEvaluation, MetricStatus
    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1, 5, 25, 100],
        soak_time_seconds=10,
        require_approval_before_final=True,
        guardrails=[
            Guardrail(name="checkout_ab", metric_source="flagsmith_experiment", comparison=">", threshold=10.0, experiment_id=1)
        ],
    )
    engine = GuardianEngine(config=cfg, flagsmith_client=fake_flagsmith)
    engine.state_machine.current_state = RolloutState.AWAITING_APPROVAL.value

    engine.latest_evaluations = [
        MetricEvaluation(
            name="checkout_ab",
            value=None,
            sample_size=2000,
            is_breached=False,
            status=MetricStatus.SRM_BREACH,
            reason="SRM Warning",
            details={"as_of": "2026-11-22T13:22:48Z", "srm_warning": True, "srm_p_value": 0.0002},
        )
    ]

    with pytest.raises(ValueError, match="Sample Ratio Mismatch \\(SRM\\) detected"):
        engine.approve()


def test_engine_approval_blocked_on_chance_to_be_worse_breach(fake_flagsmith):
    # Unit-test mock fixture: verifying approval blocked when Bayesian chance-to-be-worse breaches threshold
    from guardian.metrics.base import MetricEvaluation, MetricStatus
    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1, 5, 25, 100],
        soak_time_seconds=10,
        require_approval_before_final=True,
        guardrails=[
            Guardrail(name="checkout_ab", metric_source="flagsmith_experiment", comparison=">", threshold=10.0, experiment_id=1)
        ],
    )
    engine = GuardianEngine(config=cfg, flagsmith_client=fake_flagsmith)
    engine.state_machine.current_state = RolloutState.AWAITING_APPROVAL.value

    engine.latest_evaluations = [
        MetricEvaluation(
            name="checkout_ab",
            value=14.5,
            sample_size=2000,
            is_breached=True,
            status=MetricStatus.BREACH,
            reason="Chance-to-be-worse breach",
            details={
                "as_of": "2026-11-22T13:22:48Z",
                "chance_to_be_worse": 14.5,
                "srm_warning": False,
                "srm_p_value": 0.45,
            },
        )
    ]

    with pytest.raises(ValueError, match="Bayesian chance-to-be-worse \\(14.5%\\) exceeds configured limit"):
        engine.approve()


def test_engine_approval_succeeds_with_healthy_experiment(fake_flagsmith):
    # Unit-test mock fixture: verifying approval succeeds when experiment results pass all guardrails
    from guardian.metrics.base import MetricEvaluation, MetricStatus
    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1, 5, 25, 100],
        soak_time_seconds=10,
        require_approval_before_final=True,
        guardrails=[
            Guardrail(name="checkout_ab", metric_source="flagsmith_experiment", comparison=">", threshold=10.0, experiment_id=1)
        ],
    )
    engine = GuardianEngine(config=cfg, flagsmith_client=fake_flagsmith)
    engine.state_machine.current_state = RolloutState.AWAITING_APPROVAL.value

    engine.latest_evaluations = [
        MetricEvaluation(
            name="checkout_ab",
            value=5.8,
            sample_size=2395,
            is_breached=False,
            status=MetricStatus.HEALTHY,
            reason="Healthy metrics",
            details={
                "as_of": "2026-11-22T13:22:48Z",
                "chance_to_be_worse": 5.8,
                "srm_warning": False,
                "srm_p_value": 0.4821,
                "lift": 0.208,
            },
        )
    ]

    entry = engine.approve()
    assert entry.decision == "PROMOTED"
    assert engine.current_percentage == 100.0

    status = engine.get_status()
    assert status["experiment"] is not None
    assert status["experiment"]["as_of"] == "2026-11-22T13:22:48Z"
    assert status["experiment"]["chance_to_be_worse"] == 5.8
    assert status["experiment"]["srm_warning"] is False
    assert status["experiment"]["is_eligible_for_approval"] is True


def test_engine_reset_rejected_when_guardrail_breaching(fake_flagsmith):
    from guardian.metrics.base import MetricEvaluation, MetricStatus
    from unittest.mock import MagicMock
    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1, 5, 25, 100],
        guardrails=[
            Guardrail(name="error_rate", metric_source="prometheus", comparison=">", threshold=5.0)
        ],
    )
    mock_provider = MagicMock()
    mock_provider.evaluate_all.return_value = [
        MetricEvaluation(
            name="error_rate",
            value=12.5,
            sample_size=100,
            is_breached=True,
            status=MetricStatus.BREACH,
            reason="Metric value 12.50% > threshold 5.0% (BREACH)",
        )
    ]
    engine = GuardianEngine(config=cfg, flagsmith_client=fake_flagsmith, metric_provider=mock_provider)
    engine.state_machine.current_state = RolloutState.ROLLED_BACK.value

    with pytest.raises(ValueError, match="Cannot re-arm rollout: Guardrail 'error_rate' is currently breaching"):
        engine.reset()

    # State must stay in ROLLED_BACK
    assert engine.current_state == RolloutState.ROLLED_BACK.value


def test_engine_reset_succeeds_when_guardrails_healthy(fake_flagsmith):
    from guardian.metrics.base import MetricEvaluation, MetricStatus
    from unittest.mock import MagicMock
    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1, 5, 25, 100],
        guardrails=[
            Guardrail(name="error_rate", metric_source="prometheus", comparison=">", threshold=5.0)
        ],
    )
    mock_provider = MagicMock()
    mock_provider.evaluate_all.return_value = [
        MetricEvaluation(
            name="error_rate",
            value=0.2,
            sample_size=100,
            is_breached=False,
            status=MetricStatus.HEALTHY,
            reason="Metric value 0.20% healthy",
        )
    ]
    engine = GuardianEngine(config=cfg, flagsmith_client=fake_flagsmith, metric_provider=mock_provider)
    engine.state_machine.current_state = RolloutState.ROLLED_BACK.value

    entry = engine.reset()
    assert engine.current_state == RolloutState.PENDING.value
    assert engine.current_percentage == 0.0
    assert entry.decision == "ADVANCE"
