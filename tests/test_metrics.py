import pytest
from unittest.mock import MagicMock
from guardian.config import Guardrail, RolloutConfig
from guardian.engine import GuardianEngine
from guardian.metrics.base import MetricStatus
from guardian.metrics.prometheus import PrometheusMetricSource
from guardian.metrics.flagsmith_exp import FlagsmithExperimentMetricSource
from guardian.metrics.registry import CompositeMetricProvider


def test_prometheus_metric_source_evaluation():
    mock_session = MagicMock()
    # Mock PromQL response
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "result": [
                {"value": [1700000000, "7.5"]}
            ]
        }
    }
    mock_resp.raise_for_status.return_value = None
    mock_session.get.return_value = mock_resp

    source = PrometheusMetricSource(prometheus_url="http://mock-prom:9090", session=mock_session)
    guardrail = Guardrail(
        name="err_rate",
        metric_source="prometheus",
        comparison=">",
        threshold=5.0,
        query="rate(errors[1m])",
        min_samples=10,
        sample_size_query=None,
    )

    ev = source.evaluate(guardrail)
    assert ev.status == MetricStatus.BREACH
    assert ev.is_breached is True
    assert ev.value == 7.5


def test_prometheus_metric_source_insufficient_samples():
    mock_session = MagicMock()
    # Sample size query returns 5
    mock_sample_resp = MagicMock()
    mock_sample_resp.json.return_value = {
        "data": {"result": [{"value": [1700000000, "5"]}]}
    }
    mock_sample_resp.raise_for_status.return_value = None
    mock_session.get.return_value = mock_sample_resp

    source = PrometheusMetricSource(prometheus_url="http://mock-prom:9090", session=mock_session)
    guardrail = Guardrail(
        name="err_rate",
        metric_source="prometheus",
        comparison=">",
        threshold=5.0,
        query="rate(errors[1m])",
        sample_size_query="sum(requests)",
        min_samples=20,
    )

    ev = source.evaluate(guardrail)
    assert ev.status == MetricStatus.INSUFFICIENT_DATA
    assert ev.is_breached is False
    assert ev.sample_size == 5
    assert "Insufficient sample size" in ev.reason


def test_flagsmith_experiment_source_evaluation_and_srm():
    mock_session = MagicMock()
    # Unit-test fixture: simulated normal experiment results payload with SRM healthy
    # Note: Clearly labeled mock fixture for unit tests
    mock_get_resp = MagicMock()
    mock_get_resp.json.return_value = {
        "results": {
            "payload": {
                "srm_p_value": 0.45,
                "metrics": [
                    {
                        "metric_id": 1,
                        "variants": {
                            "control": {"n": 100, "sum": 25.0},
                            "variation_a": {"n": 100, "sum": 10.0},
                        },
                        "inference": {
                            "variation_a": {
                                "chance_to_win": 0.05,  # 5% chance to win -> 95% chance to be worse!
                                "lift": -0.20,
                            }
                        },
                    }
                ],
            }
        }
    }
    mock_get_resp.raise_for_status.return_value = None
    mock_session.get.return_value = mock_get_resp

    source = FlagsmithExperimentMetricSource(
        api_url="http://mock:8000/api/v1",
        admin_api_key="key",
        environment_key="env",
        session=mock_session,
    )

    guardrail = Guardrail(
        name="exp_chance_worse",
        metric_source="flagsmith_experiment",
        comparison=">",
        threshold=80.0,
        experiment_id=1,
        min_samples=50,
    )

    ev = source.evaluate(guardrail)
    assert ev.status == MetricStatus.BREACH
    assert ev.is_breached is True
    assert ev.value == 95.0  # (1 - 0.05) * 100
    assert ev.sample_size == 200

    # Test SRM warning breach (srm_p_value < 0.001)
    mock_get_resp.json.return_value["results"]["payload"]["srm_p_value"] = 0.00005
    ev_srm = source.evaluate(guardrail)
    assert ev_srm.is_breached is True
    assert "Sample Ratio Mismatch" in ev_srm.reason


def test_flagsmith_experiment_cooldown():
    mock_session = MagicMock()
    mock_get_resp = MagicMock()
    mock_get_resp.json.return_value = {"results": {"payload": None}}
    mock_get_resp.raise_for_status.return_value = None
    mock_session.get.return_value = mock_get_resp

    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 202
    mock_session.post.return_value = mock_post_resp

    source = FlagsmithExperimentMetricSource(
        api_url="http://mock:8000/api/v1",
        admin_api_key="key",
        environment_key="env",
        session=mock_session,
    )

    guardrail = Guardrail(name="test", metric_source="flagsmith_experiment", comparison=">", threshold=10, experiment_id=9)

    # First evaluation calls POST refresh
    source.evaluate(guardrail)
    assert mock_session.post.call_count == 1

    # Second immediate evaluation within 300s cooldown should NOT trigger POST refresh
    source.evaluate(guardrail)
    assert mock_session.post.call_count == 1


def test_composite_provider_with_engine(fake_flagsmith):
    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1, 5, 25, 100],
        soak_time_seconds=10,
        guardrails=[
            Guardrail(
                name="prom_err_rate",
                metric_source="prometheus",
                comparison=">",
                threshold=5.0,
                query="rate(errors[1m])",
                sample_size_query="sum(requests)",
                min_samples=50,
            )
        ],
    )

    mock_prom = MagicMock()
    provider = CompositeMetricProvider()
    provider.register_source("prometheus", mock_prom)

    engine = GuardianEngine(config=cfg, flagsmith_client=fake_flagsmith, metric_provider=provider)

    # Mock Insufficient Data (sample size = 15 < 50)
    mock_prom.evaluate.return_value = MagicMock(
        name="prom_err_rate",
        value=None,
        sample_size=15,
        is_breached=False,
        status=MetricStatus.INSUFFICIENT_DATA,
        reason="Insufficient sample size: 15 < 50",
        details={},
    )

    # Tick 1: PENDING -> STAGE_1
    engine.tick(now=100.0)
    assert engine.current_state == "STAGE_1"

    # Tick 2: 15s later (> soak time 10s), but sample size is still insufficient -> MUST HOLD!
    entry2 = engine.tick(now=115.0)
    assert engine.current_state == "STAGE_1"
    assert entry2.decision == "HOLD"
    assert "insufficient sample size" in entry2.reason.lower()

    # Mock Sufficient Data with a Breach (sample size 100, error_rate 12.0 > 5.0)
    mock_prom.evaluate.return_value = MagicMock(
        name="prom_err_rate",
        value=12.0,
        sample_size=100,
        is_breached=True,
        status=MetricStatus.BREACH,
        reason="Error rate 12.0% > threshold 5.0%",
        details={},
    )

    # Tick 3: Breach triggers instant rollback to 0
    entry3 = engine.tick(now=120.0)
    assert engine.current_state == "ROLLED_BACK"
    assert entry3.decision == "ROLLBACK"
    assert fake_flagsmith.current_percentage == 0.0
    assert fake_flagsmith.is_enabled is False


def test_flagsmith_experiment_collecting_data_insufficient_samples():
    """Unit test fixture: verify INSUFFICIENT_DATA status when Flagsmith is still collecting data."""
    mock_session = MagicMock()
    mock_get_resp = MagicMock()
    # Mock payload with under 50 samples in variation arm
    mock_get_resp.json.return_value = {
        "results": {
            "payload": {
                "srm_p_value": None,
                "metrics": [
                    {
                        "metric_id": 1,
                        "variants": {
                            "control": {"n": 30, "sum": 3.0},
                            "variation_a": {"n": 25, "sum": 2.0},
                        },
                        "inference": {},
                    }
                ],
            }
        }
    }
    mock_get_resp.raise_for_status.return_value = None
    mock_session.get.return_value = mock_get_resp

    source = FlagsmithExperimentMetricSource(
        api_url="http://mock:8000/api/v1",
        admin_api_key="key",
        environment_key="env",
        session=mock_session,
    )

    guardrail = Guardrail(
        name="exp_chance_worse",
        metric_source="flagsmith_experiment",
        comparison=">",
        threshold=80.0,
        experiment_id=1,
    )

    ev = source.evaluate(guardrail)
    assert ev.status == MetricStatus.INSUFFICIENT_DATA
    assert ev.is_breached is False
    assert "collecting data" in ev.reason.lower()

