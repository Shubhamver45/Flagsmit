"""
Comprehensive tests for multivariate experiment safety, rollout percentage separation,
drift/reconciliation, pause/resume, reset, emergency rollback, SQLite restart,
push telemetry, and approval gating.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from guardian.api import create_app
from guardian.audit_log import AuditLogger
from guardian.config import Guardrail, RolloutConfig
from guardian.engine import GuardianEngine
from guardian.flagsmith_client import FlagsmithAdminClient, MultivariateRolloutError
from guardian.metrics.base import MetricStatus
from guardian.metrics.push import PushMetricSource
from guardian.metrics.registry import CompositeMetricProvider
from guardian.state_machine import RolloutDecision, RolloutState
from guardian.storage import GuardianStorage


def _make_multivariate_fs(enabled: bool = True, weights=(25.0, 25.0, 25.0)):
    return {
        "id": 101,
        "environment": 7,
        "enabled": enabled,
        "multivariate_feature_state_values": [
            {"id": 1001, "multivariate_feature_option": 201, "percentage_allocation": weights[0]},
            {"id": 1002, "multivariate_feature_option": 202, "percentage_allocation": weights[1]},
            {"id": 1003, "multivariate_feature_option": 203, "percentage_allocation": weights[2]},
        ],
    }


def _client_with_fs(fs_payload: dict, flag_name: str = "exp_pulsepilot_judge_checkout_v1"):
    mock_session = MagicMock()
    feat_resp = MagicMock()
    feat_resp.json.return_value = {"results": [{"id": 55, "name": flag_name}]}
    feat_resp.raise_for_status.return_value = None

    fs_resp = MagicMock()
    fs_resp.json.return_value = {"results": [fs_payload]}
    fs_resp.raise_for_status.return_value = None

    patch_resp = MagicMock()
    patch_resp.json.return_value = {"id": fs_payload["id"], "status": "ok"}
    patch_resp.raise_for_status.return_value = None

    mock_session.get.side_effect = [feat_resp, fs_resp] * 4
    mock_session.patch.return_value = patch_resp

    client = FlagsmithAdminClient(
        api_url="https://app.flagsmith.com/api/v1",
        admin_api_key="ser.test_key",
        environment_key="env_dev",
        project_id=1,
        environment_id=7,
        session=mock_session,
    )
    return client, mock_session


def test_multivariate_feature_does_not_misread_first_variant_weight():
    fs = _make_multivariate_fs(enabled=True, weights=(25.0, 25.0, 25.0))
    client, _ = _client_with_fs(fs)
    with pytest.raises(MultivariateRolloutError):
        client.get_percentage_rollout("exp_pulsepilot_judge_checkout_v1")


def test_multivariate_disabled_reports_zero_percent():
    fs = _make_multivariate_fs(enabled=False, weights=(25.0, 25.0, 25.0))
    client, _ = _client_with_fs(fs)
    assert client.get_percentage_rollout("exp_pulsepilot_judge_checkout_v1") == 0.0


def test_multivariate_partial_rollout_refused_and_weights_preserved_on_rollback():
    fs = _make_multivariate_fs(enabled=True, weights=(25.0, 25.0, 25.0))
    client, mock_session = _client_with_fs(fs)

    # Partial percentage (e.g. 1%, 5%, 25%) must NOT overwrite single_page/express weights
    with pytest.raises(MultivariateRolloutError):
        client.set_percentage_rollout("exp_pulsepilot_judge_checkout_v1", 5.0)
    mock_session.patch.assert_not_called()

    # Emergency rollback disables flag WITHOUT sending multivariate_feature_state_values
    client.rollback_to_zero("exp_pulsepilot_judge_checkout_v1", reason="Error breach")
    mock_session.patch.assert_called_once_with(
        "https://app.flagsmith.com/api/v1/features/featurestates/101/",
        json={"environment": 7, "enabled": False},
        timeout=10.0,
    )


def test_single_variant_and_boolean_rollout_updates_accurately():
    # Single-variant rollout flag
    fs_single = {
        "id": 201,
        "environment": 7,
        "enabled": True,
        "multivariate_feature_state_values": [
            {"id": 901, "multivariate_feature_option": 301, "percentage_allocation": 5.0}
        ],
    }
    client, mock_session = _client_with_fs(fs_single)
    assert client.get_percentage_rollout("exp_pulsepilot_judge_checkout_v1") == 5.0
    client.set_percentage_rollout("exp_pulsepilot_judge_checkout_v1", 25.0)
    assert mock_session.patch.call_args.kwargs["json"]["multivariate_feature_state_values"][0]["percentage_allocation"] == 25.0

    # Boolean rollout gate flag
    fs_bool = {"id": 202, "environment": 7, "enabled": True, "multivariate_feature_state_values": []}
    bool_client, bool_session = _client_with_fs(fs_bool)
    assert bool_client.get_percentage_rollout("exp_pulsepilot_judge_checkout_v1") == 100.0
    bool_client.set_percentage_rollout("exp_pulsepilot_judge_checkout_v1", 0.0)
    assert bool_session.patch.call_args.kwargs["json"] == {"environment": 7, "enabled": False}


def test_engine_fails_safely_when_multivariate_percentage_cannot_be_determined(tmp_path):
    storage = GuardianStorage(db_path=tmp_path / "safe.db")
    cfg = RolloutConfig(
        flag_name="exp_pulsepilot_judge_checkout_v1",
        stages=[1, 5, 25, 100],
        soak_time_seconds=30,
        guardrails=[Guardrail(name="err", metric_source="push", metric="checkout", comparison=">", threshold=5.0)],
    )
    mock_client = MagicMock()
    mock_client.get_percentage_rollout.side_effect = MultivariateRolloutError("multi-variant feature")

    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=mock_client,
        audit_logger=AuditLogger(storage=storage),
        storage=storage,
    )
    assert engine.drift_flags["exp_pulsepilot_judge_checkout_v1"]["detected"] is True
    assert engine.drift_flags["exp_pulsepilot_judge_checkout_v1"]["source"] == "multivariate_safety"
    entry = engine.tick(now=1000.0)
    assert entry.decision == RolloutDecision.HOLD.value
    mock_client.set_percentage_rollout.assert_not_called()


def test_full_lifecycle_drift_reconcile_pause_resume_reset_restart_and_approval(tmp_path):
    db_path = tmp_path / "lifecycle.db"
    storage = GuardianStorage(db_path=db_path)
    state = {"pct": 0.0}

    client = MagicMock()
    client.get_percentage_rollout.side_effect = lambda _: state["pct"]
    client.set_percentage_rollout.side_effect = lambda _, p: state.__setitem__("pct", float(p)) or {"pct": p}
    client.rollback_to_zero.side_effect = lambda _, reason="": state.__setitem__("pct", 0.0) or {"pct": 0.0}

    now = [1000.0]
    push_src = PushMetricSource(clock=lambda: now[0])
    provider = CompositeMetricProvider()
    provider.register_source("push", push_src)

    cfg = RolloutConfig(
        flag_name="checkout_rollout",
        stages=[1, 5, 25, 100],
        soak_time_seconds=30,
        require_approval_before_final=True,
        guardrails=[
            Guardrail(
                name="checkout_err",
                metric_source="push",
                metric="checkout",
                comparison=">",
                threshold=5.0,
                min_samples=30,
                window_seconds=60,
            )
        ],
    )
    engine = GuardianEngine(config=cfg, flagsmith_client=client, metric_provider=provider, storage=storage)

    # 1. Start -> STAGE_1 (1%)
    e1 = engine.tick(now=now[0])
    assert e1.to_state == "STAGE_1"
    assert state["pct"] == 1.0

    # 2. Insufficient samples (<30) -> LOW SAMPLES HOLD
    push_src.ingest(metric="checkout", requests=10, errors=0, timestamp=now[0])
    now[0] += 35.0
    e_low = engine.tick(now=now[0])
    assert e_low.decision == RolloutDecision.HOLD.value
    assert engine.get_status()["guardrails"][0]["status"] == MetricStatus.INSUFFICIENT_DATA.value

    # 3. 30 requests, 0 errors -> 0% error rate, soak elapsed -> advances to STAGE_2 (5%)
    push_src.ingest(metric="checkout", requests=20, errors=0, timestamp=now[0])
    e_adv = engine.tick(now=now[0])
    assert e_adv.to_state == "STAGE_2"
    assert state["pct"] == 5.0

    # 4. External drift (Flagsmith changed to 50%) -> detected by reconcile_runtime, then resolved
    state["pct"] = 50.0
    res_drift = engine.reconcile_runtime()
    assert res_drift["checkout_rollout"]["drift"] is True
    assert engine.tick(now=now[0]).decision == RolloutDecision.HOLD.value

    state["pct"] = 5.0
    res_ok = engine.reconcile_runtime()
    assert res_ok["checkout_rollout"]["drift"] is False

    # 5. Pause & Resume
    engine.pause()
    assert engine.current_state == RolloutState.PAUSED.value
    now[0] += 40.0
    assert engine.tick(now=now[0]).decision == RolloutDecision.HOLD.value
    engine.resume()
    assert engine.current_state == "STAGE_2"

    # 6. Restart & SQLite restoration
    storage2 = GuardianStorage(db_path=db_path)
    engine2 = GuardianEngine(config=cfg, flagsmith_client=client, metric_provider=provider, storage=storage2)
    assert engine2.current_state == "STAGE_2"
    assert engine2.current_percentage == 5.0

    # 7. Advance to STAGE_3 (25%) -> AWAITING_APPROVAL -> approve -> PROMOTED (100%)
    now[0] += 5.0
    push_src.ingest(metric="checkout", requests=30, errors=0, timestamp=now[0])
    assert engine2.tick(now=now[0]).to_state == "STAGE_3"
    now[0] += 35.0
    push_src.ingest(metric="checkout", requests=30, errors=0, timestamp=now[0])
    assert engine2.tick(now=now[0]).to_state == RolloutState.AWAITING_APPROVAL.value

    engine2.approve(now=now[0])
    assert engine2.current_state == RolloutState.PROMOTED.value
    assert state["pct"] == 100.0

    # 8. Error rate > 5% triggers emergency rollback to 0%
    push_src.ingest(metric="checkout", requests=30, errors=10, timestamp=now[0])
    now[0] += 61.0  # expire old healthy samples, ingest failing batch
    push_src.ingest(metric="checkout", requests=30, errors=5, timestamp=now[0])
    e_rb = engine2.tick(now=now[0])
    assert e_rb.decision == RolloutDecision.ROLLBACK.value
    assert engine2.current_state == RolloutState.ROLLED_BACK.value
    assert state["pct"] == 0.0

    # 9. Operator reset blocked while breaching, succeeds once healthy
    with pytest.raises(ValueError, match="Cannot re-arm rollout"):
        engine2.reset()

    now[0] += 61.0
    push_src.ingest(metric="checkout", requests=30, errors=0, timestamp=now[0])
    engine2.reset()
    assert engine2.current_state == RolloutState.PENDING.value
    assert state["pct"] == 0.0


def test_metrics_ingest_endpoint_auth_and_soak_gating(tmp_path):
    storage = GuardianStorage(db_path=tmp_path / "ingest.db")
    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0
    push_src = PushMetricSource()
    provider = CompositeMetricProvider()
    provider.register_source("push", push_src)
    cfg = RolloutConfig(
        flag_name="checkout_rollout",
        stages=[1, 5, 25, 100],
        soak_time_seconds=30,
        guardrails=[
            Guardrail(
                name="checkout_err",
                metric_source="push",
                metric="checkout",
                comparison=">",
                threshold=5.0,
                min_samples=30,
                window_seconds=60,
            )
        ],
    )
    engine = GuardianEngine(config=cfg, flagsmith_client=mock_client, metric_provider=provider, storage=storage)
    app = create_app(engine, api_token="op-secret")
    c = TestClient(app)

    assert c.post("/metrics/ingest", json={"metric": "checkout", "requests": 30, "errors": 0}).status_code == 401
    r = c.post(
        "/metrics/ingest",
        json={"metric": "checkout", "requests": 30, "errors": 0},
        headers={"Authorization": "Bearer op-secret"},
    )
    assert r.status_code == 200
    ev = push_src.evaluate(cfg.guardrails[0])
    assert ev.value == 0.0
    assert ev.sample_size == 30
    assert ev.status == MetricStatus.HEALTHY


def test_v2_feature_versioning_fallback_publishes_version():
    fs_single = {
        "id": 1899299,
        "feature": 308340,
        "environment": 7,
        "enabled": False,
        "feature_state_value": "off",
        "multivariate_feature_state_values": [
            {"id": 5001, "multivariate_feature_option": {"id": 6001, "value": "on"}, "percentage_allocation": 0.0}
        ],
    }
    client, mock_session = _client_with_fs(fs_single, flag_name="pulsepilot_checkout_rollout_v1")
    v2_err_resp = MagicMock()
    v2_err_resp.ok = False
    v2_err_resp.status_code = 400
    v2_err_resp.text = '{"detail":"This environment uses v2 feature versioning. Use the environment feature version endpoint instead."}'
    mock_session.patch.return_value = v2_err_resp

    v2_ok_resp = MagicMock()
    v2_ok_resp.ok = True
    v2_ok_resp.json.return_value = {"uuid": "ver-1", "published": True}
    mock_session.post.return_value = v2_ok_resp

    res = client.set_percentage_rollout("pulsepilot_checkout_rollout_v1", 1.0)
    assert res["published"] is True
    mock_session.post.assert_called_once_with(
        "https://app.flagsmith.com/api/v1/environments/7/features/308340/versions/",
        json={
            "publish_immediately": True,
            "feature_states_to_update": [
                {
                    "id": 1899299,
                    "enabled": True,
                    "feature_state_value": "off",
                    "multivariate_feature_state_values": [
                        {"id": 5001, "multivariate_feature_option": 6001, "percentage_allocation": 1.0}
                    ],
                }
            ],
        },
        timeout=10.0,
    )
