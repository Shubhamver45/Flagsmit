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


def test_collapse_consecutive_identical_hold_decisions(tmp_path):
    db_file = tmp_path / "collapse_test.db"
    storage = GuardianStorage(db_path=db_file)

    cfg = RolloutConfig(
        flag_name="collapse_flag",
        stages=[1, 5, 25, 100],
        soak_time_seconds=60,
    )
    client = MockFlagsmithClient()
    engine = GuardianEngine(config=cfg, flagsmith_client=client, storage=storage, skip_startup_reconciliation=True)

    # Initial tick: PENDING -> STAGE_1
    t0 = 1000.0
    engine.tick(now=t0)
    entries = storage.get_audit_entries(flag_name="collapse_flag")
    assert len(entries) == 1
    assert entries[0]["decision"] == "ADVANCE"

    # 10 consecutive ticks while soaking in STAGE_1 (each produces HOLD)
    for i in range(1, 11):
        engine.tick(now=t0 + i)

    # Should have collapsed consecutive HOLDs into a single row!
    entries_after = storage.get_audit_entries(flag_name="collapse_flag")
    assert len(entries_after) == 2  # 1 ADVANCE + 1 collapsed HOLD
    hold_entry = entries_after[1]
    assert hold_entry["decision"] == "HOLD"
    assert hold_entry["count"] == 10
    assert hold_entry["first_timestamp"] is not None
    assert hold_entry["last_timestamp"] is not None

    # Verify bounded pruning keeps SQLite bounded
    for i in range(50):
        storage.record_audit(
            timestamp=f"2026-01-01T00:00:{i:02d}",
            flag_name="dummy_flag",
            from_state="A",
            to_state="B",
            decision="ADVANCE",
            reason=f"step {i}",
            numbers={},
        )
    storage.prune_audit_log(max_entries=25)
    all_entries = storage.get_audit_entries()
    assert len(all_entries) <= 25

    storage.close()


def test_status_endpoint_full_vs_default_20(tmp_path):
    db_file = tmp_path / "status_full_test.db"
    storage = GuardianStorage(db_path=db_file)

    cfg = RolloutConfig(flag_name="status_test_flag", stages=[1, 5, 25, 100])
    client = MockFlagsmithClient()
    engine = GuardianEngine(config=cfg, flagsmith_client=client, storage=storage, skip_startup_reconciliation=True)

    # Insert 35 distinct decision entries
    for i in range(35):
        storage.record_audit(
            timestamp=f"2026-01-01T00:{i:02d}:00",
            flag_name="status_test_flag",
            from_state="S1",
            to_state="S2",
            decision=f"DEC_{i}",
            reason=f"reason {i}",
            numbers={"step": i},
        )

    # Default call to get_status returns last 20 entries
    default_status = engine.get_status(full=False)
    assert len(default_status["timeline"]) == 20
    # Must be in chronological order ending with DEC_34
    assert default_status["timeline"][-1]["decision"] == "DEC_34"
    assert default_status["timeline"][0]["decision"] == "DEC_15"

    # Full=True returns all 35
    full_status = engine.get_status(full=True)
    assert len(full_status["timeline"]) == 35
    assert full_status["timeline"][0]["decision"] == "DEC_0"
    assert full_status["timeline"][-1]["decision"] == "DEC_34"

    storage.close()


def test_require_winner_gate_semantics(tmp_path):
    db_file = tmp_path / "winner_gate_test.db"
    storage = GuardianStorage(db_path=db_file)

    # 1. Config with require_winner = True
    cfg_winner = RolloutConfig(
        flag_name="winner_flag",
        stages=[1, 5, 25, 100],
        require_winner=True,
    )
    client = MockFlagsmithClient()
    engine = GuardianEngine(config=cfg_winner, flagsmith_client=client, storage=storage, skip_startup_reconciliation=True)

    # Save experiment results with inconclusive / negative interval: chance_to_win=0.92 (<0.95), ci_low=-0.05
    storage.save_experiment_results("winner_flag", {
        "as_of": "2026-01-01T00:00:00Z",
        "chance_to_win": 0.92,
        "chance_to_be_worse": 8.0,
        "ci_low": -0.05,
        "ci_high": 0.50,
        "srm_p_value": 0.50,
    })

    eligible, reason = engine.check_approval_eligibility("winner_flag")
    assert eligible is False
    assert "require_winner is enabled" in reason

    # Save experiment results with decisive win: chance_to_win=0.99 (>=0.95), ci_low=0.20 (>0)
    storage.save_experiment_results("winner_flag", {
        "as_of": "2026-01-01T00:01:00Z",
        "chance_to_win": 0.99,
        "chance_to_be_worse": 1.0,
        "ci_low": 0.20,
        "ci_high": 0.80,
        "srm_p_value": 0.50,
    })

    eligible, reason = engine.check_approval_eligibility("winner_flag")
    assert eligible is True
    assert reason is None

    # 2. Config with default require_winner = False (no evidence of harm)
    cfg_default = RolloutConfig(
        flag_name="harm_flag",
        stages=[1, 5, 25, 100],
        require_winner=False,
    )
    engine_default = GuardianEngine(config=cfg_default, flagsmith_client=client, storage=storage, skip_startup_reconciliation=True)

    # chance_to_win=0.92 (ci_low=-0.05), but chance_to_be_worse=8.0% <= threshold 10%
    storage.save_experiment_results("harm_flag", {
        "as_of": "2026-01-01T00:02:00Z",
        "chance_to_win": 0.92,
        "chance_to_be_worse": 8.0,
        "ci_low": -0.05,
        "ci_high": 0.50,
        "srm_p_value": 0.50,
    })
    eligible_harm, _ = engine_default.check_approval_eligibility("harm_flag")
    assert eligible_harm is True

    storage.close()


def test_webhook_drift_deduplication(tmp_path):
    db_file = tmp_path / "webhook_dedup_test.db"
    storage = GuardianStorage(db_path=db_file)

    cfg = RolloutConfig(flag_name="dedup_flag", stages=[1, 5, 25, 100])
    client = MockFlagsmithClient(initial_percentages={"dedup_flag": 1.0})
    engine = GuardianEngine(config=cfg, flagsmith_client=client, storage=storage, skip_startup_reconciliation=True)

    # First webhook: out-of-band change to 50%
    payload = {
        "event_type": "FLAG_UPDATED",
        "data": {
            "changed_by": "ops-engineer",
            "new_state": {
                "enabled": True,
                "feature": {"name": "dedup_flag"},
                "multivariate_feature_state_values": [{"percentage_allocation": 50.0}],
            },
        },
    }
    res1 = engine.handle_flagsmith_webhook(payload)
    assert res1["status"] == "drift_detected"

    # Second identical webhook arrives immediately (duplicate delivery)
    res2 = engine.handle_flagsmith_webhook(payload)
    assert res2["status"] == "drift_already_active"

    # Verify only ONE audit entry was recorded for DRIFT
    drift_entries = [e for e in storage.get_audit_entries(flag_name="dedup_flag") if e["decision"] == "DRIFT"]
    assert len(drift_entries) == 1

    storage.close()


def test_write_ledger_echo_immunity_and_late_webhooks(tmp_path):
    db_file = tmp_path / "echo_test.db"
    storage = GuardianStorage(db_path=db_file)

    cfg = RolloutConfig(
        flag_name="echo_flag",
        stages=[1, 5, 25, 100],
        soak_time_seconds=1,
        echo_window_seconds=30.0,
    )
    client = MockFlagsmithClient(initial_percentages={"echo_flag": 0.0})
    engine = GuardianEngine(config=cfg, flagsmith_client=client, storage=storage, skip_startup_reconciliation=True)

    # 1. Simulate full progression 1 -> 5 -> 25 -> 100
    now = time.time()
    # Write Stage 1 (1%) at t0
    storage.record_write("echo_flag", 1.0, written_at=now - 20)
    # Write Stage 2 (5%) at t0 + 10s
    storage.record_write("echo_flag", 5.0, written_at=now - 10)
    # Write Stage 3 (25%) at t0 + 15s
    storage.record_write("echo_flag", 25.0, written_at=now - 5)
    engine.state_machine.restore_state("STAGE_3", 2, now - 5)
    storage.save_rollout_state("echo_flag", "STAGE_3", 2, now - 5, 25.0)

    # Webhook arrives late: a webhook for 5.0% delivered 5s late while Guardian is at 25.0%
    late_webhook_payload = {
        "event_type": "FLAG_UPDATED",
        "data": {
            "changed_by": "Guardian Admin Key",
            "new_state": {
                "enabled": True,
                "feature": {"name": "echo_flag"},
                "multivariate_feature_state_values": [{"percentage_allocation": 5.0}],
            },
        },
    }
    res = engine.handle_flagsmith_webhook(late_webhook_payload)
    assert res["status"] == "ok"
    assert engine.drift_flags["echo_flag"]["detected"] is False

    # Also verify reconciler runtime does not flag 5.0% if it observed it within echo window
    client.percentages["echo_flag"] = 5.0
    reconcile_res = engine.reconcile_runtime()
    assert engine.drift_flags["echo_flag"]["detected"] is False

    # 2. Real out-of-band change by ops-engineer: 50.0% (a value Guardian never wrote)
    ops_webhook_payload = {
        "event_type": "FLAG_UPDATED",
        "data": {
            "changed_by": "ops-engineer",
            "new_state": {
                "enabled": True,
                "feature": {"name": "echo_flag"},
                "multivariate_feature_state_values": [{"percentage_allocation": 50.0}],
            },
        },
    }
    res_ops = engine.handle_flagsmith_webhook(ops_webhook_payload)
    assert res_ops["status"] == "drift_detected"
    assert engine.drift_flags["echo_flag"]["detected"] is True

    # 3. An old write outside the echo window (written 40s ago when window is 30s)
    storage.record_write("echo_flag", 12.0, written_at=now - 40)
    assert storage.is_recent_write("echo_flag", 12.0, window_seconds=30.0, now=now) is False

    storage.close()


def test_exact_value_display_and_require_winner_boundaries(tmp_path):
    db_file = tmp_path / "display_boundary_test.db"
    storage = GuardianStorage(db_path=db_file)

    cfg = RolloutConfig(
        flag_name="boundary_flag",
        stages=[1, 5, 25, 100],
        require_winner=True,
    )
    client = MockFlagsmithClient(initial_percentages={"boundary_flag": 25.0})
    engine = GuardianEngine(config=cfg, flagsmith_client=client, storage=storage, skip_startup_reconciliation=True)

    # Test exact rounding in display: 0.9339 -> 93.4%, 0.0838 -> 8.4%
    storage.save_experiment_results("boundary_flag", {
        "as_of": "2026-01-01T00:00:00Z",
        "chance_to_win": 0.9339,
        "chance_to_be_worse": 6.6,
        "ci_low": 0.15,
        "ci_high": 0.85,
        "lift": 0.425,
        "srm_p_value": 0.50,
    })
    status = engine.get_status("boundary_flag")
    cwin = status["experiment"]["chance_to_win"]
    assert cwin == 0.9339
    # Format exactly as demo.sh does:
    cwin_str = f"{cwin*100:.1f}%" if cwin <= 1.0 else f"{cwin:.1f}%"
    assert cwin_str == "93.4%"

    # 0.0838 -> 8.4%
    storage.save_experiment_results("boundary_flag", {
        "as_of": "2026-01-01T00:00:00Z",
        "chance_to_win": 0.0838,
        "chance_to_be_worse": 91.6,
        "ci_low": -0.50,
        "ci_high": 0.10,
        "srm_p_value": 0.50,
    })
    status = engine.get_status("boundary_flag")
    cwin_worse = status["experiment"]["chance_to_win"]
    assert cwin_worse == 0.0838
    cwin_worse_str = f"{cwin_worse*100:.1f}%" if cwin_worse <= 1.0 else f"{cwin_worse:.1f}%"
    assert cwin_worse_str == "8.4%"

    # Test require_winner boundary: 0.949 (blocked) vs 0.951 (cleared)
    storage.save_experiment_results("boundary_flag", {
        "as_of": "2026-01-01T00:00:00Z",
        "chance_to_win": 0.949,
        "chance_to_be_worse": 5.1,
        "ci_low": 0.10,
        "ci_high": 0.60,
        "srm_p_value": 0.50,
    })
    eligible_949, reason_949 = engine.check_approval_eligibility("boundary_flag")
    assert eligible_949 is False
    assert "chance to win 94.9% < 95.0%" in reason_949

    storage.save_experiment_results("boundary_flag", {
        "as_of": "2026-01-01T00:00:00Z",
        "chance_to_win": 0.951,
        "chance_to_be_worse": 4.9,
        "ci_low": 0.10,
        "ci_high": 0.60,
        "srm_p_value": 0.50,
    })
    eligible_951, reason_951 = engine.check_approval_eligibility("boundary_flag")
    assert eligible_951 is True
    assert reason_951 is None

    # Even if chance_to_win is 0.99, if ci_low <= 0, require_winner blocks
    storage.save_experiment_results("boundary_flag", {
        "as_of": "2026-01-01T00:00:00Z",
        "chance_to_win": 0.99,
        "chance_to_be_worse": 1.0,
        "ci_low": -0.01,
        "ci_high": 0.80,
        "srm_p_value": 0.50,
    })
    eligible_ci, reason_ci = engine.check_approval_eligibility("boundary_flag")
    assert eligible_ci is False
    assert "CI low" in reason_ci

    storage.close()
