"""
Mandatory live integration test against a real self-hosted Flagsmith instance.
Run with:
  FLAGSMITH_URL=http://localhost:8000/api/v1 \
  FLAGSMITH_ADMIN_API_KEY=... \
  FLAGSMITH_ENVIRONMENT_KEY=... \
  pytest -m live tests/test_flagsmith_live_integration.py
"""

import os
import pytest
from dotenv import dotenv_values

from guardian.config import Guardrail, RolloutConfig
from guardian.engine import GuardianEngine
from guardian.flagsmith_client import FlagsmithAdminClient
from guardian.metrics.push import PushMetricSource
from guardian.metrics.registry import CompositeMetricProvider
from guardian.state_machine import RolloutState
from guardian.storage import GuardianStorage


@pytest.mark.live
def test_live_flagsmith_10_step_rollout_and_rollback(tmp_path):
    env_file = dotenv_values(".env")
    api_url = os.environ.get("FLAGSMITH_URL") or env_file.get("FLAGSMITH_URL")
    admin_key = os.environ.get("FLAGSMITH_ADMIN_API_KEY") or env_file.get("FLAGSMITH_ADMIN_API_KEY")
    env_key = os.environ.get("FLAGSMITH_ENVIRONMENT_KEY") or env_file.get("FLAGSMITH_ENVIRONMENT_KEY")
    flag_name = os.environ.get("FLAG_NAME") or env_file.get("FLAG_NAME", "new_checkout")

    if not (api_url and admin_key and env_key):
        pytest.skip("Live Flagsmith credentials not configured in environment or .env")

    client = FlagsmithAdminClient(api_url=api_url, admin_api_key=admin_key, environment_key=env_key)

    # Step 4: Read feature through Guardian
    initial = client.get_environment_feature_state(flag_name)
    assert "id" in initial

    # Step 3 & 5: Connect Guardian and advance allocation to 5%
    push_src = PushMetricSource()
    provider = CompositeMetricProvider()
    provider.register_source("push", push_src)
    storage = GuardianStorage(db_path=tmp_path / "live_int.db")

    cfg = RolloutConfig(
        flag_name=flag_name,
        stages=[5, 25, 100],
        soak_time_seconds=0,
        guardrails=[
            Guardrail(
                name="live_push_error_rate",
                metric_source="push",
                metric="checkout_live_test",
                comparison=">",
                threshold=5.0,
                min_samples=10,
                window_seconds=60,
            )
        ],
    )
    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=client,
        metric_provider=provider,
        storage=storage,
        skip_startup_reconciliation=True,
    )
    engine.tick()
    assert engine.state_machines[flag_name].current_state == "STAGE_1"

    # Step 6: Verify changed allocation directly through Flagsmith Admin API
    assert client.get_percentage_rollout(flag_name) == pytest.approx(5.0, abs=0.1)

    # Step 7 & 8: Introduce controlled metric breach and trigger Guardian rollback
    push_src.ingest("checkout_live_test", requests=50, errors=20)
    engine.tick()
    assert engine.state_machines[flag_name].current_state == RolloutState.ROLLED_BACK.value

    # Step 9: Verify actual Flagsmith allocation is 0%
    assert client.get_percentage_rollout(flag_name) == pytest.approx(0.0, abs=0.1)

    # Step 10: Verify audit event persisted in SQLite with valid hash chain
    entries = storage.get_audit_entries(flag_name=flag_name)
    assert any(e["decision"] == "ROLLBACK" for e in entries)
    assert storage.verify_audit_chain()["valid"] is True
    storage.close()
