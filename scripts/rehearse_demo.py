#!/usr/bin/env python3
"""
Automated Demo Rehearsal Script.
Runs the entire healthy rollout -> human approval -> fault injection -> auto-rollback
cycle 3 consecutive times against the live local Flagsmith and demo stack to detect
and fix any flakiness.
"""

import os
import sys
import time
import requests
from guardian.main import load_env_file
load_env_file()

from guardian.config import load_config
from guardian.flagsmith_client import FlagsmithAdminClient
from guardian.metrics.prometheus import PrometheusMetricSource
from guardian.metrics.flagsmith_exp import FlagsmithExperimentMetricSource
from guardian.metrics.registry import CompositeMetricProvider
from guardian.audit_log import AuditLogger
from guardian.actions.alerts import AlertDispatcher
from guardian.actions.idempotency import IdempotencyManager
from guardian.engine import GuardianEngine

FLAGSMITH_URL = os.environ.get("FLAGSMITH_URL", "http://localhost:8000/api/v1")
ADMIN_KEY = os.environ.get("FLAGSMITH_ADMIN_API_KEY", "")
ENV_KEY = os.environ.get("FLAGSMITH_ENVIRONMENT_KEY", "")
DEMO_APP_URL = "http://localhost:5001"
PROMETHEUS_URL = "http://localhost:9090"


def send_traffic(count: int = 60, bug_enabled: bool = False):
    requests.post(f"{DEMO_APP_URL}/toggle-bug?enabled={'1' if bug_enabled else '0'}", timeout=3)
    identities = [f"user_{i}" for i in range(1, 201)]
    errors = 0
    for i in range(count):
        uid = identities[i % len(identities)]
        try:
            r = requests.post(f"{DEMO_APP_URL}/checkout", json={"user_id": uid}, timeout=3)
            if r.status_code >= 400:
                errors += 1
        except Exception:
            errors += 1
    # Give Prometheus 2.5s to scrape
    time.sleep(2.5)
    return errors


def run_single_rehearsal(iteration: int) -> bool:
    print(f"\n{'='*70}")
    print(f"🎬 REHEARSAL RUN #{iteration}")
    print(f"{'='*70}")

    # 1. Reset Demo App & Flagsmith State
    print("1. Resetting demo app metrics & setting bug to OFF...")
    requests.post(f"{DEMO_APP_URL}/reset-metrics", timeout=3)
    requests.post(f"{DEMO_APP_URL}/toggle-bug?enabled=0", timeout=3)

    from dataclasses import replace
    cfg = load_config("config.yaml")
    cfg = replace(cfg, soak_time_seconds=1)  # Fast soak for test automation

    client = FlagsmithAdminClient(
        api_url=FLAGSMITH_URL,
        admin_api_key=ADMIN_KEY,
        environment_key=ENV_KEY,
    )
    client.rollback_to_zero(cfg.flag_name, reason="Rehearsal initial reset")

    provider = CompositeMetricProvider()
    provider.register_source("prometheus", PrometheusMetricSource(prometheus_url=PROMETHEUS_URL))
    provider.register_source("flagsmith_experiment", FlagsmithExperimentMetricSource(
        api_url=FLAGSMITH_URL,
        admin_api_key=ADMIN_KEY,
        environment_key=ENV_KEY,
    ))

    logger = AuditLogger()
    idemp = IdempotencyManager()
    idemp.reset()
    alerts = AlertDispatcher()

    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=client,
        audit_logger=logger,
        metric_provider=provider,
        alert_dispatcher=alerts,
        idempotency_manager=idemp,
    )

    # 2. Healthy Rollout Flow
    print("2. Sending healthy traffic (bug OFF)...")
    send_traffic(count=60, bug_enabled=False)

    print("3. Stepping through rollout stages...")
    # Tick 1: PENDING -> STAGE_1 (1%)
    e1 = engine.tick()
    print(f"   Tick 1: State={engine.current_state} ({engine.current_percentage}%) | Decision={e1.decision}")
    assert engine.current_state == "STAGE_1", f"Expected STAGE_1, got {engine.current_state}"
    assert engine.current_percentage == 1.0

    # Wait soak time
    time.sleep(1.2)
    # Tick 2: STAGE_1 -> STAGE_2 (5%)
    e2 = engine.tick()
    print(f"   Tick 2: State={engine.current_state} ({engine.current_percentage}%) | Decision={e2.decision}")
    assert engine.current_state == "STAGE_2", f"Expected STAGE_2, got {engine.current_state}"

    time.sleep(1.2)
    # Tick 3: STAGE_2 -> STAGE_3 (25%)
    e3 = engine.tick()
    print(f"   Tick 3: State={engine.current_state} ({engine.current_percentage}%) | Decision={e3.decision}")
    assert engine.current_state == "STAGE_3", f"Expected STAGE_3, got {engine.current_state}"

    time.sleep(1.2)
    # Tick 4: STAGE_3 -> AWAITING_APPROVAL
    e4 = engine.tick()
    print(f"   Tick 4: State={engine.current_state} ({engine.current_percentage}%) | Decision={e4.decision}")
    assert engine.current_state == "AWAITING_APPROVAL", f"Expected AWAITING_APPROVAL, got {engine.current_state}"

    # 4. Human Approval
    print("4. Executing Operator Human Approval...")
    e_app = engine.approve()
    print(f"   Approval: State={engine.current_state} ({engine.current_percentage}%) | Decision={e_app.decision}")
    assert engine.current_state == "PROMOTED", f"Expected PROMOTED, got {engine.current_state}"
    assert engine.current_percentage == 100.0

    # Verify Flagsmith remote flag state
    fs_state = client.get_percentage_rollout(cfg.flag_name)
    print(f"   Flagsmith remote allocation confirmed at {fs_state}%")
    assert fs_state == 100.0

    # 5. Fault Injection & Auto-Rollback Flow
    print("5. Resetting state for fault injection demonstration...")
    engine.reset()
    requests.post(f"{DEMO_APP_URL}/reset-metrics", timeout=3)
    time.sleep(1)

    print("6. Turning BUG_IN_NEW_CHECKOUT ON and sending failure traffic...")
    err_count = send_traffic(count=80, bug_enabled=True)
    print(f"   Fault traffic sent: {err_count} errors generated")

    print("7. Evaluating Guardrail Breach...")
    # First tick starts at STAGE_1
    engine.tick()
    # Next tick evaluates Prometheus error rate
    e_breach = engine.tick()
    print(f"   Breach Tick: State={engine.current_state} ({engine.current_percentage}%) | Decision={e_breach.decision}")
    print(f"   Reason: {e_breach.reason}")

    assert engine.current_state == "ROLLED_BACK", f"Expected ROLLED_BACK, got {engine.current_state}"
    assert engine.current_percentage == 0.0

    fs_rollback_state = client.get_percentage_rollout(cfg.flag_name)
    print(f"   Flagsmith remote allocation confirmed at {fs_rollback_state}%")
    assert fs_rollback_state == 0.0

    print(f"✔ REHEARSAL RUN #{iteration} PASSED CLEANLY (Zero flakiness, 100% assertions satisfied)")
    return True


def main():
    print("========================================================================")
    print("🚀 GUARDIAN DEMO AUTOMATED REHEARSAL (3 CONSECUTIVE RUNS)")
    print("========================================================================")

    for i in range(1, 4):
        ok = run_single_rehearsal(i)
        if not ok:
            print(f"❌ Rehearsal #{i} failed!")
            sys.exit(1)
        time.sleep(1)

    print("\n" + "=" * 70)
    print("🎉 ALL 3 REHEARSALS COMPLETED SUCCESSFULLY WITH ZERO FLAKINESS!")
    print("========================================================================")


if __name__ == "__main__":
    main()
