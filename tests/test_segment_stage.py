"""
Tests for the optional, off-by-default pre-percentage Flagsmith segment stage.
Covers both disabled (default) and enabled cases with mocked APIs.
"""

from unittest.mock import MagicMock

from guardian.config import Guardrail, RolloutConfig
from guardian.engine import GuardianEngine
from guardian.flagsmith_client import FlagsmithAdminClient
from guardian.metrics.base import MetricEvaluation, MetricStatus
from guardian.storage import GuardianStorage


def test_segment_stage_disabled_by_default(tmp_path):
    storage = GuardianStorage(db_path=tmp_path / "seg_off.db")
    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0

    cfg = RolloutConfig(flag_name="new_checkout", stages=[1, 25, 100], soak_time_seconds=0)
    assert cfg.segment is None

    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=mock_client,
        metric_provider=lambda: {},
        storage=storage,
        skip_startup_reconciliation=True,
    )
    try:
        engine.tick()
        assert engine.state_machines["new_checkout"].current_state == "STAGE_1"
        mock_client.enable_segment_override.assert_not_called()
        mock_client.set_percentage_rollout.assert_called_with("new_checkout", 1.0)

        engine.manual_rollback(flag_name="new_checkout", reason="test")
        mock_client.remove_segment_override.assert_not_called()
    finally:
        storage.close()


def test_segment_stage_enabled_progression_and_rollback_removes_override(tmp_path):
    storage = GuardianStorage(db_path=tmp_path / "seg_on.db")
    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0

    breach_flag = [False]

    class FakeProvider:
        def evaluate_all(self, guardrails):
            if breach_flag[0]:
                return [
                    MetricEvaluation(
                        name="err_rate",
                        value=12.0,
                        sample_size=100,
                        is_breached=True,
                        status=MetricStatus.BREACH,
                        reason="Metric value 12.00% > threshold 5.0% (BREACH)",
                        details={"threshold": 5.0},
                    )
                ]
            return [
                MetricEvaluation(
                    name="err_rate",
                    value=0.5,
                    sample_size=100,
                    is_breached=False,
                    status=MetricStatus.HEALTHY,
                    reason="Healthy",
                )
            ]

    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1, 25, 100],
        soak_time_seconds=0,
        segment="internal-team",
        segment_value="new_flow",
        guardrails=[Guardrail(name="err_rate", metric_source="mock", comparison=">", threshold=5.0)],
    )
    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=mock_client,
        metric_provider=FakeProvider(),
        storage=storage,
        skip_startup_reconciliation=True,
    )
    try:
        # Tick 1: PENDING -> SEGMENT_STAGE
        engine.tick()
        assert engine.state_machines["new_checkout"].current_state == "SEGMENT_STAGE"
        assert engine.state_machines["new_checkout"].current_percentage == 0.0
        mock_client.enable_segment_override.assert_called_once_with(
            "new_checkout", "internal-team", enabled=True, value="new_flow"
        )
        mock_client.set_percentage_rollout.assert_not_called()

        # Tick 2: soak=0 healthy -> SEGMENT_STAGE -> STAGE_1 (1%)
        engine.tick()
        assert engine.state_machines["new_checkout"].current_state == "STAGE_1"
        mock_client.set_percentage_rollout.assert_called_with("new_checkout", 1.0)

        # Tick 3: Guardrail breach -> ROLLED_BACK and removes segment override
        breach_flag[0] = True
        engine.tick()
        assert engine.state_machines["new_checkout"].current_state == "ROLLED_BACK"
        mock_client.rollback_to_zero.assert_called_once()
        mock_client.remove_segment_override.assert_called_once_with("new_checkout", "internal-team")
    finally:
        storage.close()


def test_flagsmith_client_segment_override_http_calls():
    mock_session = MagicMock()

    def fake_get(url, **kwargs):
        resp = MagicMock()
        if url.endswith("/projects/1/features/"):
            resp.json.return_value = {"results": [{"id": 10, "name": "new_checkout"}], "next": None}
        elif url.endswith("/projects/1/segments/"):
            resp.json.return_value = {"results": [{"id": 77, "name": "internal-team"}], "next": None}
        elif url.endswith("/features/feature-segments/"):
            resp.json.return_value = {"results": [{"id": 501, "feature": 10, "segment": 77}]}
        elif url.endswith("/features/featurestates/"):
            resp.json.return_value = {"results": []}
        elif "/environments/env-key/featurestates/" in url:
            resp.json.return_value = {"results": [{"id": 100, "feature": 10, "enabled": False, "multivariate_feature_state_values": []}]}
        return resp

    def fake_post(url, **kwargs):
        resp = MagicMock()
        resp.json.return_value = {"id": 901, "enabled": True}
        return resp

    mock_session.get.side_effect = fake_get
    mock_session.post.side_effect = fake_post
    mock_session.delete.return_value = MagicMock()

    client = FlagsmithAdminClient(
        api_url="http://localhost:8000/api/v1",
        admin_api_key="test-key",
        environment_key="env-key",
        project_id=1,
        environment_id=2,
        session=mock_session,
    )
    res = client.enable_segment_override("new_checkout", "internal-team", enabled=True, value="new_flow")
    assert res["enabled"] is True
    assert mock_session.post.call_args.kwargs["json"]["feature_segment"] == 501

    removed = client.remove_segment_override("new_checkout", "internal-team")
    assert removed is True
    mock_session.delete.assert_called_once()
    assert mock_session.delete.call_args.args[0].endswith("/features/feature-segments/501/")


def test_segment_stage_echo_immunity_and_restart_recovery(tmp_path):
    db_path = tmp_path / "seg_restart.db"
    storage1 = GuardianStorage(db_path=db_path)
    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0

    cfg = RolloutConfig(
        flag_name="new_checkout",
        stages=[1, 25, 100],
        soak_time_seconds=60,
        segment="internal-team",
        segment_value="new_flow",
    )
    engine1 = GuardianEngine(
        config=cfg,
        flagsmith_client=mock_client,
        metric_provider=lambda: {},
        storage=storage1,
        skip_startup_reconciliation=True,
    )
    engine1.tick()
    assert engine1.state_machines["new_checkout"].current_state == "SEGMENT_STAGE"
    assert engine1.get_status()["segment"] == "internal-team"

    # Webhook echo arriving right after Guardian's own segment write (including 100% on segment override)
    # must NOT trigger false DRIFT
    wh_res = engine1.handle_flagsmith_webhook({
        "event_type": "FLAG_UPDATED",
        "data": {
            "new_state": {
                "feature": {"name": "new_checkout"},
                "enabled": True,
                "multivariate_feature_state_values": [{"percentage_allocation": 100.0}],
            },
            "changed_by": "Guardian Admin Key",
        },
    })
    assert engine1.drift_flags["new_checkout"]["detected"] is False
    assert wh_res.get("status") in ("ok", "echo_suppressed", "reconciled", "ignored")
    storage1.close()

    # Restart Guardian with the same SQLite DB: recovers to SEGMENT_STAGE and does not duplicate overrides
    storage2 = GuardianStorage(db_path=db_path)
    engine2 = GuardianEngine(
        config=cfg,
        flagsmith_client=mock_client,
        metric_provider=lambda: {},
        storage=storage2,
        skip_startup_reconciliation=False,
    )
    assert engine2.state_machines["new_checkout"].current_state == "SEGMENT_STAGE"
    assert engine2.drift_flags["new_checkout"]["detected"] is False
    # Calling tick() while soaking in SEGMENT_STAGE must NOT call enable_segment_override again
    mock_client.enable_segment_override.reset_mock()
    engine2.tick()
    mock_client.enable_segment_override.assert_not_called()
    storage2.close()
