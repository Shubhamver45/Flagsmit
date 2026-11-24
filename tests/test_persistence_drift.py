"""
Tests for Guardian SQLite Persistence, Multi-Rollout Management,
and Startup Reconciliation / Drift Detection.
"""

import os
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from guardian.config import GuardianConfig, Guardrail, RolloutConfig
from guardian.engine import GuardianEngine
from guardian.flagsmith_client import FlagsmithClientProtocol
from guardian.state_machine import RolloutDecision, RolloutState
from guardian.storage import GuardianStorage


class MockFlagsmithClient:
    def __init__(self, initial_percentages=None):
        self.percentages = initial_percentages or {}
        self.rollback_calls = []
        self.set_calls = []

    def get_percentage_rollout(self, flag_name: str) -> float:
        return float(self.percentages.get(flag_name, 0.0))

    def set_percentage_rollout(self, flag_name: str, percentage: float):
        self.percentages[flag_name] = float(percentage)
        self.set_calls.append((flag_name, percentage))
        return {"status": "ok", "percentage": percentage}

    def rollback_to_zero(self, flag_name: str, reason: str = ""):
        self.percentages[flag_name] = 0.0
        self.rollback_calls.append((flag_name, reason))
        return {"status": "rolled_back", "percentage": 0.0}


def test_sqlite_storage_basics(tmp_path):
    db_file = tmp_path / "test_guardian.db"
    storage = GuardianStorage(db_path=db_file)

    # 1. Rollout state
    storage.save_rollout_state(
        flag_name="flag_x",
        current_state="STAGE_2",
        current_stage_index=1,
        stage_entered_at=1000.0,
        current_percentage=5.0,
        is_approved=False,
    )

    state = storage.load_rollout_state("flag_x")
    assert state is not None
    assert state["flag_name"] == "flag_x"
    assert state["current_state"] == "STAGE_2"
    assert state["current_stage_index"] == 1
    assert state["stage_entered_at"] == 1000.0
    assert state["current_percentage"] == 5.0

    # 2. Audit log
    entry_id = storage.record_audit(
        timestamp="2026-11-24T12:00:00Z",
        flag_name="flag_x",
        from_state="STAGE_1",
        to_state="STAGE_2",
        decision="ADVANCE",
        reason="Soak completed",
        numbers={"target_percentage": 5},
    )
    assert entry_id > 0
    entries = storage.get_audit_entries(flag_name="flag_x")
    assert len(entries) == 1
    assert entries[0]["decision"] == "ADVANCE"
    assert entries[0]["numbers"]["target_percentage"] == 5

    # 3. Idempotency
    key = "flag_x:ADVANCE:STAGE_2:1"
    assert not storage.is_action_executed(key)
    storage.mark_action_executed(key, "flag_x", "ADVANCE", "STAGE_2", 1)
    assert storage.is_action_executed(key)

    # 4. Experiment results
    storage.save_experiment_results("flag_x", {"as_of": "2026-11-24", "lift": 0.25, "srm_p_value": 0.85})
    exp = storage.load_experiment_results("flag_x")
    assert exp is not None
    assert exp["lift"] == 0.25
    assert exp["srm_p_value"] == 0.85

    storage.close()


def test_kill_and_restart_mid_stage_resumes_correctly(tmp_path):
    db_file = tmp_path / "resume_guardian.db"
    storage1 = GuardianStorage(db_path=db_file)

    cfg = RolloutConfig(
        flag_name="checkout_v2",
        stages=[1, 5, 25, 100],
        soak_time_seconds=60,
        require_approval_before_final=True,
    )
    client = MockFlagsmithClient(initial_percentages={"checkout_v2": 0.0})

    # Engine 1 starts rollout
    engine1 = GuardianEngine(
        config=cfg,
        flagsmith_client=client,
        storage=storage1,
    )

    t0 = 1000.0
    # Step 1: PENDING -> STAGE_1 (1%)
    e1 = engine1.tick(now=t0)
    assert e1.decision == RolloutDecision.ADVANCE.value
    assert engine1.current_state == "STAGE_1"
    assert client.get_percentage_rollout("checkout_v2") == 1.0

    # Advance after 60s soak to STAGE_2 (5%)
    t1 = t0 + 65.0
    e2 = engine1.tick(now=t1)
    assert e2.decision == RolloutDecision.ADVANCE.value
    assert engine1.current_state == "STAGE_2"
    assert client.get_percentage_rollout("checkout_v2") == 5.0
    assert engine1.state_machine.stage_entered_at == t1

    # Simulate running 20s into STAGE_2 soak (still soaking, 20s < 60s)
    t_mid = t1 + 20.0
    e3 = engine1.tick(now=t_mid)
    assert e3.decision == RolloutDecision.HOLD.value

    # KILL ENGINE 1 (close DB connection, destroy instance)
    storage1.close()
    del engine1

    # SIMULATE RESTART: Create Engine 2 from the same SQLite DB
    storage2 = GuardianStorage(db_path=db_file)
    engine2 = GuardianEngine(
        config=cfg,
        flagsmith_client=client,
        storage=storage2,
    )

    # Verify reconciliation resumed at STAGE_2 with original stage_entered_at
    assert engine2.current_state == "STAGE_2"
    assert engine2.current_percentage == 5.0
    assert engine2.state_machine.stage_entered_at == t1
    assert not engine2.drift_flags["checkout_v2"]["detected"]

    # 10s after restart (t1 + 30s): Still soaking (30s < 60s), MUST NOT restart from PENDING
    e_mid_restart = engine2.tick(now=t1 + 30.0)
    assert e_mid_restart.decision == RolloutDecision.HOLD.value
    assert engine2.current_state == "STAGE_2"

    # 65s after STAGE_2 start (t1 + 65s): Soak finishes, advances to STAGE_3 (25%)
    e_advance = engine2.tick(now=t1 + 65.0)
    assert e_advance.decision == RolloutDecision.ADVANCE.value
    assert engine2.current_state == "STAGE_3"
    assert client.get_percentage_rollout("checkout_v2") == 25.0

    storage2.close()


def test_two_rollouts_in_parallel(tmp_path):
    db_file = tmp_path / "multi_rollout.db"
    storage = GuardianStorage(db_path=db_file)

    cfg1 = RolloutConfig(
        flag_name="flag_alpha",
        stages=[5, 50, 100],
        soak_time_seconds=10,
    )
    cfg2 = RolloutConfig(
        flag_name="flag_beta",
        stages=[10, 25, 100],
        soak_time_seconds=20,
    )
    multi_cfg = GuardianConfig(rollouts=[cfg1, cfg2], db_path=str(db_file))

    client = MockFlagsmithClient(initial_percentages={
        "flag_alpha": 0.0,
        "flag_beta": 0.0,
    })

    engine = GuardianEngine(
        config=multi_cfg,
        flagsmith_client=client,
        storage=storage,
    )

    assert "flag_alpha" in engine.rollout_configs
    assert "flag_beta" in engine.rollout_configs

    t0 = 2000.0
    # Tick starts both from PENDING -> STAGE_1
    engine.tick(now=t0)
    assert engine.state_machines["flag_alpha"].current_state == "STAGE_1"
    assert engine.state_machines["flag_beta"].current_state == "STAGE_1"
    assert client.get_percentage_rollout("flag_alpha") == 5.0
    assert client.get_percentage_rollout("flag_beta") == 10.0

    # At t0 + 12s: flag_alpha (soak=10s) advances to STAGE_2 (50%)
    # flag_beta (soak=20s) is still soaking in STAGE_1 (10%)
    engine.tick(now=t0 + 12.0)
    assert engine.state_machines["flag_alpha"].current_state == "STAGE_2"
    assert engine.state_machines["flag_beta"].current_state == "STAGE_1"
    assert client.get_percentage_rollout("flag_alpha") == 50.0
    assert client.get_percentage_rollout("flag_beta") == 10.0

    # At t0 + 25s: flag_beta advances to STAGE_2 (25%)
    engine.tick(now=t0 + 25.0)
    assert engine.state_machines["flag_beta"].current_state == "STAGE_2"
    assert client.get_percentage_rollout("flag_beta") == 25.0

    # Verify status reports both separately
    status = engine.get_status()
    assert "rollouts" in status
    assert "flag_alpha" in status["rollouts"]
    assert "flag_beta" in status["rollouts"]
    assert status["rollouts"]["flag_alpha"]["percentage"] == 50.0
    assert status["rollouts"]["flag_beta"]["percentage"] == 25.0

    storage.close()


def test_startup_reconciliation_and_drift_detection(tmp_path):
    db_file = tmp_path / "drift_guardian.db"
    storage = GuardianStorage(db_path=db_file)

    cfg = RolloutConfig(
        flag_name="search_v2",
        stages=[1, 5, 25, 100],
        soak_time_seconds=30,
    )

    # Pre-seed SQLite state saying search_v2 is at STAGE_2 (5%)
    storage.save_rollout_state(
        flag_name="search_v2",
        current_state="STAGE_2",
        current_stage_index=1,
        stage_entered_at=5000.0,
        current_percentage=5.0,
        is_approved=False,
    )

    # Simulate Flagsmith has DRIFTED out of band to 50%
    client = MockFlagsmithClient(initial_percentages={"search_v2": 50.0})

    # Start Guardian: Startup reconciliation must detect the discrepancy!
    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=client,
        storage=storage,
    )

    # 1. Drift must be detected
    drift = engine.drift_flags["search_v2"]
    assert drift["detected"] is True
    assert drift["actual_percentage"] == 50.0
    assert drift["expected_percentage"] == 5.0
    assert drift["stored_state"] == "STAGE_2"

    # 2. On tick, engine must HOLD and NOT act blindly
    entry = engine.tick(now=5100.0)
    assert entry.decision == RolloutDecision.HOLD.value
    assert "[DRIFT WARNING]" in entry.reason
    # Flagsmith percentage remains untouched at 50%
    assert client.get_percentage_rollout("search_v2") == 50.0

    # 3. Status API reports drift
    status = engine.get_status()
    assert status["drift"]["detected"] is True
    assert "does not match" in status["drift"]["message"]

    # 4. Operator Reset clears drift and resets safely back to PENDING (0%)
    reset_entry = engine.reset(flag_name="search_v2")
    assert reset_entry.to_state == RolloutState.PENDING.value
    assert engine.drift_flags["search_v2"]["detected"] is False
    assert client.get_percentage_rollout("search_v2") == 0.0

    # After reset, status no longer has drift
    status_after = engine.get_status()
    assert status_after["drift"]["detected"] is False

    storage.close()
