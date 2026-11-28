from unittest.mock import MagicMock
import pytest

from guardian.config import Guardrail, RolloutConfig
from guardian.engine import GuardianEngine
from guardian.metrics.base import MetricStatus
from guardian.metrics.flagsmith_exp import FlagsmithExperimentMetricSource
from guardian.metrics.registry import CompositeMetricProvider
from guardian.state_machine import RolloutState
from guardian.storage import GuardianStorage


def _exp_guardrail(min_samples: int = 50, action_on_srm: str = "HOLD", require_winner: bool = False):
    return Guardrail(
        name="exp_gate",
        metric_source="flagsmith_experiment",
        metric_id=1,
        comparison=">",
        threshold=10.0,
        min_samples=min_samples,
        action_on_srm=action_on_srm,
        require_winner=require_winner,
    )


def _mock_source_with_results(results_payload: dict) -> FlagsmithExperimentMetricSource:
    sess = MagicMock()
    exp_list = MagicMock(status_code=200)
    exp_list.json.return_value = {"results": [{"id": 1, "name": "Checkout A/B", "status": "running"}]}
    res_resp = MagicMock(status_code=200)
    res_resp.json.return_value = results_payload
    sess.get.side_effect = [exp_list, res_resp]
    sess.post.return_value = MagicMock(status_code=202)
    return FlagsmithExperimentMetricSource(
        api_url="http://localhost:8000/api/v1",
        admin_api_key="adm",
        environment_key="env",
        session=sess,
    )


def test_valid_experiment_results_healthy():
    src = _mock_source_with_results({
        "results": {
            "as_of": "2026-11-28T09:48:39Z",
            "srm_p_value": 0.42,
            "payload": {
                "srm_p_value": 0.42,
                "metrics": [{
                    "metric_id": 1,
                    "variants": {
                        "control": {"n": 200, "sum": 50.0},
                        "variation": {"n": 205, "sum": 140.0},
                    },
                    "inference": {"variation": {"chance_to_win": 0.999, "lift": 1.73, "ci_low": 1.1, "ci_high": 2.3}},
                }],
            },
        }
    })
    ev = src.evaluate(_exp_guardrail(min_samples=50))
    assert ev.status == MetricStatus.HEALTHY
    assert ev.is_breached is False
    assert ev.details["chance_to_win"] == pytest.approx(0.999)
    assert ev.details["srm_warning"] is False


def test_insufficient_sample_size_and_missing_exposure_data():
    # Insufficient sample size (< min_samples=50 per arm)
    src_low = _mock_source_with_results({
        "results": {
            "payload": {
                "metrics": [{
                    "metric_id": 1,
                    "variants": {"control": {"n": 12, "sum": 3.0}, "variation": {"n": 10, "sum": 6.0}},
                    "inference": {"variation": {"chance_to_win": 0.8}},
                }],
            }
        }
    })
    ev_low = src_low.evaluate(_exp_guardrail(min_samples=50))
    assert ev_low.status == MetricStatus.INSUFFICIENT_DATA
    assert ev_low.is_breached is False

    # Missing exposure data
    src_empty = _mock_source_with_results({"results": {"payload": {"metrics": []}}})
    ev_empty = src_empty.evaluate(_exp_guardrail())
    assert ev_empty.status == MetricStatus.INSUFFICIENT_DATA


def test_srm_detection_and_invalid_experiment_data():
    # Severe SRM (p < 0.001)
    src_srm = _mock_source_with_results({
        "results": {
            "payload": {
                "srm_p_value": 0.000002,
                "metrics": [{
                    "metric_id": 1,
                    "variants": {"control": {"n": 100, "sum": 25.0}, "variation": {"n": 300, "sum": 75.0}},
                    "inference": {"variation": {"chance_to_win": 0.5}},
                }],
            }
        }
    })
    ev_srm = src_srm.evaluate(_exp_guardrail(action_on_srm="HOLD"))
    assert ev_srm.status == MetricStatus.SRM_BREACH
    assert ev_srm.details["srm_warning"] is True

    # Invalid / malformed JSON error from Flagsmith results endpoint
    sess = MagicMock()
    exp_ok = MagicMock(status_code=200, json=lambda: {"results": [{"id": 1, "name": "Checkout A/B", "status": "running"}]})
    bad_res = MagicMock(status_code=200)
    bad_res.json.side_effect = ValueError("malformed json")
    sess.get.side_effect = [exp_ok, bad_res]
    src_bad = FlagsmithExperimentMetricSource("http://localhost:8000/api/v1", "adm", "env", session=sess)
    ev_bad = src_bad.evaluate(_exp_guardrail())
    assert ev_bad.status == MetricStatus.ERROR


def test_srm_blocks_promotion_and_human_approval_gate_succeeds_when_clear(tmp_path):
    fs = MagicMock()
    fs.get_percentage_rollout.return_value = 0.0
    storage = GuardianStorage(db_path=tmp_path / "exp_gate.db")

    src_srm = _mock_source_with_results({
        "results": {
            "payload": {
                "srm_p_value": 0.000002,
                "metrics": [{
                    "metric_id": 1,
                    "variants": {"control": {"n": 100, "sum": 25.0}, "variation": {"n": 300, "sum": 75.0}},
                    "inference": {"variation": {"chance_to_win": 0.5}},
                }],
            }
        }
    })
    provider = CompositeMetricProvider()
    provider.register_source("flagsmith_experiment", src_srm)

    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[25, 100],
        soak_time_seconds=0,
        require_approval_before_final=True,
        guardrails=[_exp_guardrail(action_on_srm="HOLD")],
    )
    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=fs,
        metric_provider=provider,
        storage=storage,
        skip_startup_reconciliation=True,
    )
    # Advance to STAGE_1 (25%) then AWAITING_APPROVAL
    engine.tick()
    # Re-prime mock for second tick
    src_srm.session.get.side_effect = [
        MagicMock(status_code=200, json=lambda: {"results": [{"id": 1, "name": "Checkout A/B", "status": "running"}]}),
        MagicMock(status_code=200, json=lambda: {
            "results": {
                "payload": {
                    "srm_p_value": 0.000002,
                    "metrics": [{
                        "metric_id": 1,
                        "variants": {"control": {"n": 100, "sum": 25.0}, "variation": {"n": 300, "sum": 75.0}},
                        "inference": {"variation": {"chance_to_win": 0.5}},
                    }],
                }
            }
        }),
    ]
    engine.tick()
    assert engine.state_machines["new_checkout"].current_state == RolloutState.AWAITING_APPROVAL.value

    # SRM blocks approval
    with pytest.raises(ValueError, match="SRM"):
        engine.approve(flag_name="new_checkout")

    # Now supply healthy experiment results -> human approval succeeds -> PROMOTED
    src_srm.session.get.side_effect = [
        MagicMock(status_code=200, json=lambda: {"results": [{"id": 1, "name": "Checkout A/B", "status": "running"}]}),
        MagicMock(status_code=200, json=lambda: {
            "results": {
                "payload": {
                    "srm_p_value": 0.25,
                    "metrics": [{
                        "metric_id": 1,
                        "variants": {"control": {"n": 218, "sum": 63.0}, "variation": {"n": 193, "sum": 147.0}},
                        "inference": {"variation": {"chance_to_win": 0.9999, "lift": 1.63, "ci_low": 1.04, "ci_high": 2.22}},
                    }],
                }
            }
        }),
    ]
    engine.tick()
    res = engine.approve(flag_name="new_checkout")
    assert res.to_state == RolloutState.PROMOTED.value
    storage.close()
