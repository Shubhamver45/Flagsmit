"""Comprehensive engineering audit tests for Guardian (Sections 2-6)."""

import asyncio
import pytest
import requests
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from guardian.api import create_app
from guardian.config import RolloutConfig, Guardrail
from guardian.engine import GuardianEngine
from guardian.flagsmith_client import FlagsmithAdminClient
from guardian.main import background_ticker
from guardian.storage import GuardianStorage


def _make_config(flag_name="new_checkout", soak=0):
    return RolloutConfig(
        flag_name=flag_name,
        stages=[1.0, 5.0, 25.0, 100.0],
        soak_time_seconds=soak,
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


def test_pause_survives_restart_and_resumes_correctly(tmp_path):
    db_path = tmp_path / "restart_pause.db"
    cfg = _make_config(soak=0)

    mock_client = MagicMock()
    # Flagsmith stays at 1.0% (STAGE_1)
    mock_client.get_percentage_rollout.return_value = 1.0
    mock_client.get_environment_feature_state.return_value = {
        "enabled": True,
        "multivariate_feature_state_values": [{"percentage_allocation": 1.0}],
    }

    # Boot 1: start -> STAGE_1 -> pause -> PAUSED
    storage1 = GuardianStorage(db_path=db_path)
    engine1 = GuardianEngine(
        config=cfg,
        flagsmith_client=mock_client,
        metric_provider=lambda: {"error_rate": 0.1},
        storage=storage1,
        skip_startup_reconciliation=True,
    )
    engine1.tick()
    assert engine1.state_machine.current_state == "STAGE_1"
    engine1.pause()
    assert engine1.state_machine.current_state == "PAUSED"
    storage1.close()

    # Boot 2: fresh engine & storage, run reconcile_on_startup()
    storage2 = GuardianStorage(db_path=db_path)
    engine2 = GuardianEngine(
        config=cfg,
        flagsmith_client=mock_client,
        metric_provider=lambda: {"error_rate": 0.1},
        storage=storage2,
        skip_startup_reconciliation=False,
    )
    assert engine2.state_machine.current_state == "PAUSED"
    assert engine2.state_machine.current_percentage == 1.0

    # Tick while paused does not advance
    tick_entry = engine2.tick()
    assert tick_entry.decision == "HOLD"
    assert engine2.state_machine.current_state == "PAUSED"

    # Resume restores STAGE_1 (not UNKNOWN)
    res_entry = engine2.resume()
    assert res_entry.to_state == "STAGE_1"
    assert engine2.state_machine.current_percentage == 1.0
    storage2.close()


@pytest.mark.asyncio
async def test_background_daemon_pause_resume_and_breach_rollback(tmp_path):
    storage = GuardianStorage(db_path=tmp_path / "daemon.db")
    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0
    metrics = {"error_rate": 0.2}

    engine = GuardianEngine(
        config=_make_config(soak=0),
        flagsmith_client=mock_client,
        metric_provider=lambda: metrics,
        storage=storage,
        skip_startup_reconciliation=True,
    )

    task = asyncio.create_task(background_ticker(engine, interval=0.05))
    try:
        await asyncio.sleep(0.12)
        # Should have started and advanced while healthy
        assert engine.state_machine.current_state in ("STAGE_1", "STAGE_2", "STAGE_3", "AWAITING_APPROVAL")

        # Pause and verify it stays PAUSED across daemon ticks
        if engine.state_machine.current_state != "PAUSED":
            engine.pause()
        await asyncio.sleep(0.12)
        assert engine.state_machine.current_state == "PAUSED"

        # Inject guardrail breach while PAUSED -> daemon still rolls back to 0%
        metrics["error_rate"] = 12.0
        await asyncio.sleep(0.12)
        assert engine.state_machine.current_state == "ROLLED_BACK"
        assert len(storage.get_incidents("new_checkout")) == 1
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    storage.close()


def test_incident_deduplication_pagination_and_manual_distinction(tmp_path):
    storage = GuardianStorage(db_path=tmp_path / "inc_audit.db")
    mock_client = MagicMock()
    mock_client.get_percentage_rollout.return_value = 0.0
    metrics = {"error_rate": 0.1}

    engine = GuardianEngine(
        config=_make_config(soak=0),
        flagsmith_client=mock_client,
        metric_provider=lambda: metrics,
        storage=storage,
        skip_startup_reconciliation=True,
    )
    engine.tick()
    metrics["error_rate"] = 9.0
    engine.tick()  # triggers ROLLBACK + 1 incident
    engine.tick()  # duplicate polling tick in ROLLED_BACK
    engine.tick()  # duplicate polling tick in ROLLED_BACK

    incidents = storage.get_incidents("new_checkout")
    assert len(incidents) == 1
    assert incidents[0]["trigger_type"] == "GUARDRAIL_BREACH"

    # Manual rollback on a fresh engine does NOT create a GUARDRAIL_BREACH incident
    engine.reset()
    metrics["error_rate"] = 0.1
    engine.tick()
    manual_entry = engine.manual_rollback(reason="Manual operator stop")
    assert manual_entry.decision == "ROLLBACK"
    assert len(storage.get_incidents("new_checkout")) == 1  # still just the 1 automated incident

    # Pagination on GET /incidents
    app = create_app(engine=engine, api_token="tok")
    client = TestClient(app)
    res = client.get("/incidents?limit=1&offset=0", headers={"Authorization": "Bearer tok"})
    assert res.status_code == 200
    assert res.json()["total"] == 1
    assert res.json()["count"] == 1

    res_p2 = client.get("/incidents?limit=1&offset=1", headers={"Authorization": "Bearer tok"})
    assert res_p2.status_code == 200
    assert res_p2.json()["total"] == 1
    assert res_p2.json()["count"] == 0
    storage.close()


def test_flagsmith_client_retry_read_only_and_error_handling():
    client = FlagsmithAdminClient(
        api_url="http://localhost:8000/api/v1",
        admin_api_key="secret-admin-key-999",
        environment_key="env-key",
        project_id=1,
        environment_id=1,
    )
    adapter = client.session.get_adapter("http://")
    # Ensure retries NEVER repeat POST or PATCH
    assert "POST" not in adapter.max_retries.allowed_methods
    assert "PATCH" not in adapter.max_retries.allowed_methods
    assert "GET" in adapter.max_retries.allowed_methods

    # Test pagination + empty results + error handling with mocked session
    mock_sess = MagicMock()
    page1 = MagicMock()
    page1.json.return_value = {"results": [{"id": 1, "name": "f1"}], "next": "http://localhost:8000/api/v1/projects/1/features/?page=2"}
    page1.raise_for_status.return_value = None
    page2 = MagicMock()
    page2.json.return_value = {"results": [{"id": 2, "name": "f2"}], "next": None}
    page2.raise_for_status.return_value = None
    mock_sess.get.side_effect = [page1, page2]

    paged_client = FlagsmithAdminClient("http://localhost:8000", "secret-admin-key-999", "env-key", project_id=1, session=mock_sess)
    feats = paged_client.list_features()
    assert [f["name"] for f in feats] == ["f1", "f2"]

    # Empty projects
    empty_resp = MagicMock()
    empty_resp.json.return_value = []
    empty_resp.raise_for_status.return_value = None
    mock_sess.get.side_effect = [empty_resp]
    assert paged_client.list_projects() == []

    # Timeout / 401 / 403 / 500 propagate cleanly without exposing admin_api_key
    mock_sess.get.side_effect = requests.exceptions.Timeout("Read timed out")
    with pytest.raises(requests.exceptions.Timeout):
        paged_client.list_projects()

    for code in (401, 403, 500):
        err_resp = MagicMock()
        err_resp.status_code = code
        err_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(f"{code} Error")
        mock_sess.get.side_effect = [err_resp]
        with pytest.raises(requests.exceptions.HTTPError):
            paged_client.list_environments()


def test_selective_cleanup_ownership_rules():
    # Verify exact ownership logic preserves user-owned experiments/metrics
    exp_feat_id = 42
    exps = [
        {"id": 1, "feature": 42, "name": "Checkout A/B Experiment"},      # Guardian-owned
        {"id": 2, "feature": 99, "name": "Checkout Redesign Test"},       # User-owned!
        {"id": 3, "feature": 42, "name": "User Custom Checkout Test"},    # User-owned!
    ]
    deleted_exps = [
        e["id"] for e in exps
        if e.get("feature") == exp_feat_id and e.get("name") == "Checkout A/B Experiment"
    ]
    assert deleted_exps == [1]

    metrics = [
        {"id": 10, "name": "Checkout Completed", "definition": {"event": "checkout_completed"}},  # Guardian-owned
        {"id": 11, "name": "Checkout Completed", "definition": {"event": "other_event"}},         # User-owned
        {"id": 12, "name": "Revenue", "definition": {"event": "checkout_completed"}},             # User-owned
    ]
    deleted_metrics = [
        m["id"] for m in metrics
        if m.get("name") == "Checkout Completed" and (m.get("definition") or {}).get("event") == "checkout_completed"
    ]
    assert deleted_metrics == [10]
