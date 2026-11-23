#!/usr/bin/env python3
"""
Verification Script: Test Guardian Reusability on a Freshly Created Flagsmith Project and Flag.
Demonstrates that Guardian is completely decoupled from the demo checkout setup and
can manage any arbitrary project and flag on Flagsmith.
"""

import os
import sys
import time
import json
import subprocess
import requests
from pathlib import Path
from dotenv import dotenv_values

from guardian.config import load_config
from guardian.flagsmith_client import FlagsmithAdminClient
from guardian.audit_log import AuditLogger
from guardian.actions.alerts import AlertDispatcher
from guardian.metrics.registry import CompositeMetricProvider
from guardian.metrics.base import MetricEvaluation, MetricStatus
from guardian.engine import GuardianEngine


class MockMetricSource:
    """Mock metric source for testing custom guardrails on second project."""
    def __init__(self, healthy: bool = True):
        self.healthy = healthy

    def evaluate(self, guardrail):
        if self.healthy:
            return MetricEvaluation(
                name=guardrail.name,
                value=0.2,
                sample_size=100,
                is_breached=False,
                status=MetricStatus.HEALTHY,
                reason="Metric healthy: 0.2% < 5.0%",
            )
        else:
            return MetricEvaluation(
                name=guardrail.name,
                value=14.5,
                sample_size=100,
                is_breached=True,
                status=MetricStatus.BREACH,
                reason="Metric breach: 14.5% > 5.0%",
            )


def main():
    print("========================================================================")
    print("🚀 GUARDIAN REUSABILITY VERIFICATION: FRESH PROJECT & FLAG LIFECYCLE")
    print("========================================================================")

    env = dotenv_values(".env")
    flagsmith_url = env.get("FLAGSMITH_URL", "http://localhost:8000/api/v1")
    admin_api_key = env.get("FLAGSMITH_ADMIN_API_KEY")
    if not admin_api_key:
        print("❌ Error: FLAGSMITH_ADMIN_API_KEY missing from .env", file=sys.stderr)
        sys.exit(1)

    headers = {
        "Authorization": f"Api-Key {admin_api_key}",
        "Content-Type": "application/json",
    }

    # Step 1: Discover Organisation and Setup Second Project in Flagsmith
    print("\n--- Step 1: Provisioning Second Project, Environment, and Flag in Flagsmith ---")
    setup_code = """
from organisations.models import Organisation
from projects.models import Project
from environments.models import Environment
from features.models import Feature, FeatureState
from features.multivariate.models import MultivariateFeatureOption

org = Organisation.objects.first()
project, _ = Project.objects.get_or_create(
    name="E-Commerce Recommendation Engine",
    organisation=org,
)
env, _ = Environment.objects.get_or_create(
    name="Production",
    project=project,
)
feature, _ = Feature.objects.get_or_create(
    project=project,
    name="v2_recommendations_model",
    defaults={
        "type": "MULTIVARIATE",
        "default_enabled": False,
        "description": "V2 Deep Learning recommendation ranking model",
    }
)
opt, _ = MultivariateFeatureOption.objects.get_or_create(
    feature=feature,
    string_value="variation",
    defaults={"type": "unicode", "default_percentage_allocation": 0}
)
fs, _ = FeatureState.objects.get_or_create(
    feature=feature,
    environment=env,
    defaults={"enabled": False}
)
fs.multivariate_feature_state_values.get_or_create(
    multivariate_feature_option=opt,
    defaults={"percentage_allocation": 0}
)

import json
print("=== SECOND_PROJECT_INFO_START ===")
print(json.dumps({
    "project_id": project.id,
    "project_name": project.name,
    "environment_id": env.id,
    "environment_name": env.name,
    "environment_key": env.api_key,
    "flag_name": feature.name,
}))
print("=== SECOND_PROJECT_INFO_END ===")
"""
    cmd = ["docker", "compose", "exec", "-T", "flagsmith", "python", "manage.py", "shell", "-c", setup_code]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if "=== SECOND_PROJECT_INFO_START ===" not in res.stdout:
        print(f"❌ Failed to set up second project:\n{res.stderr or res.stdout}", file=sys.stderr)
        sys.exit(1)

    info_str = res.stdout.split("=== SECOND_PROJECT_INFO_START ===")[1].split("=== SECOND_PROJECT_INFO_END ===")[0].strip()
    proj_info = json.loads(info_str)
    second_project_id = proj_info["project_id"]
    second_project_name = proj_info["project_name"]
    second_env_id = proj_info["environment_id"]
    second_env_name = proj_info["environment_name"]
    second_env_key = proj_info["environment_key"]
    second_flag_name = proj_info["flag_name"]

    print(f"✔ Second Project Ready!")
    print(f"  • Project Name     : {second_project_name} (ID: {second_project_id})")
    print(f"  • Environment Name : {second_env_name} (ID: {second_env_id})")
    print(f"  • Environment Key  : {second_env_key}")
    print(f"  • Feature Flag     : '{second_flag_name}'")

    # Step 5: Execute `guardian init` via CLI targeting the new project and flag
    print("\n--- Step 5: Executing `guardian init` via CLI for the Second Project ---")
    config_output = "config.second_project.yaml"
    init_cmd = [
        sys.executable, "-m", "guardian.cli", "init",
        "--flagsmith-url", flagsmith_url,
        "--admin-api-key", admin_api_key,
        "--environment-key", second_env_key,
        "--flag-name", second_flag_name,
        "--stages", "1, 10, 50, 100",
        "--soak", "1",
        "-o", config_output,
        "--non-interactive",
    ]
    sub_env = {**os.environ, "PYTHONPATH": "."}
    proc = subprocess.run(init_cmd, capture_output=True, text=True, env=sub_env)
    print(proc.stdout)
    if proc.returncode != 0:
        print(f"❌ `guardian init` failed:\n{proc.stderr}", file=sys.stderr)
        sys.exit(1)

    assert Path(config_output).exists(), f"Expected {config_output} to be created!"
    print(f"✔ Verified {config_output} was generated by `guardian init`.")

    # Step 6: Initialize Guardian with the Generated Config
    print("\n--- Step 6: Initializing Guardian Engine with the New Project Config ---")
    cfg = load_config(config_output)
    assert cfg.flag_name == second_flag_name
    assert cfg.environment_key == second_env_key
    assert cfg.stages == [1, 10, 50, 100]

    client = FlagsmithAdminClient(
        api_url=flagsmith_url,
        admin_api_key=admin_api_key,
        environment_key=second_env_key,
        project_id=second_project_id,
        environment_id=second_env_id,
    )

    # Ensure initial allocation is reset to 0.0%
    client.rollback_to_zero(second_flag_name, reason="Initial verification reset")
    init_pct = client.get_percentage_rollout(second_flag_name)
    print(f"  • Flagsmith Remote Feature Allocation initially: {init_pct}%")
    assert init_pct == 0.0

    mock_source = MockMetricSource(healthy=True)
    provider = CompositeMetricProvider()
    provider.register_source("prometheus", mock_source)
    provider.register_source("flagsmith_experiment", mock_source)

    logger = AuditLogger()
    alerts = AlertDispatcher()
    engine = GuardianEngine(
        config=cfg,
        flagsmith_client=client,
        audit_logger=logger,
        metric_provider=provider,
        alert_dispatcher=alerts,
    )

    # Step 7: Phased Rollout Execution
    print("\n--- Step 7: Executing Phased Rollout Stages ---")
    # Tick 1: PENDING -> STAGE_1 (1%)
    e1 = engine.tick()
    print(f"  [Tick 1] Decision={e1.decision} | State: {engine.current_state} ({engine.current_percentage}%)")
    remote_1 = client.get_percentage_rollout(second_flag_name)
    print(f"  ➔ Verified remote Flagsmith allocation updated to: {remote_1}%")
    assert remote_1 == 1.0

    # Wait soak
    time.sleep(1.2)
    # Tick 2: STAGE_1 -> STAGE_2 (10%)
    e2 = engine.tick()
    print(f"  [Tick 2] Decision={e2.decision} | State: {engine.current_state} ({engine.current_percentage}%)")
    remote_2 = client.get_percentage_rollout(second_flag_name)
    print(f"  ➔ Verified remote Flagsmith allocation updated to: {remote_2}%")
    assert remote_2 == 10.0

    # Wait soak
    time.sleep(1.2)
    # Tick 3: STAGE_2 -> STAGE_3 (50%)
    e3 = engine.tick()
    print(f"  [Tick 3] Decision={e3.decision} | State: {engine.current_state} ({engine.current_percentage}%)")
    remote_3 = client.get_percentage_rollout(second_flag_name)
    print(f"  ➔ Verified remote Flagsmith allocation updated to: {remote_3}%")
    assert remote_3 == 50.0

    # Wait soak
    time.sleep(1.2)
    # Tick 4: STAGE_3 -> AWAITING_APPROVAL (holds at 50%)
    e4 = engine.tick()
    print(f"  [Tick 4] Decision={e4.decision} | State: {engine.current_state} ({engine.current_percentage}%)")
    assert engine.current_state == "AWAITING_APPROVAL"
    remote_hold = client.get_percentage_rollout(second_flag_name)
    print(f"  ➔ Holding at {remote_hold}% before final stage: Human operator approval strictly required.")
    assert remote_hold == 50.0

    # Step 8: Human Operator Signoff / Promotion
    print("\n--- Step 8: Human Operator Promotion Approval ---")
    app_entry = engine.approve()
    print(f"  [Approve] Decision={app_entry.decision} | State: {engine.current_state} ({engine.current_percentage}%)")
    assert engine.current_state == "PROMOTED"
    remote_promoted = client.get_percentage_rollout(second_flag_name)
    print(f"  ➔ Verified remote Flagsmith allocation promoted to: {remote_promoted}%")
    assert remote_promoted == 100.0

    # Step 9: Injected Guardrail Failure & Instant Automated Rollback
    print("\n--- Step 9: Injected Fault & Instant Automated Rollback ---")
    mock_source.healthy = False  # Simulate guardrail breach
    rb_entry = engine.tick()
    print(f"  [Breach Tick] Decision={rb_entry.decision} | State: {engine.current_state} ({engine.current_percentage}%)")
    print(f"  ➔ Breach Reason: {rb_entry.reason}")
    assert engine.current_state == "ROLLED_BACK"
    remote_rollback = client.get_percentage_rollout(second_flag_name)
    print(f"  ➔ Verified remote Flagsmith allocation instantly rolled back to: {remote_rollback}%")
    assert remote_rollback == 0.0

    # Clean up generated config file
    if os.path.exists(config_output):
        os.remove(config_output)

    print("\n========================================================================")
    print("🎉 FULL LIFECYCLE VERIFICATION SUCCEEDED ON SECOND PROJECT & FLAG!")
    print(f"  • Second Project ID : {second_project_id} ('{second_project_name}')")
    print(f"  • Environment Key   : {second_env_key}")
    print(f"  • Feature Flag      : '{second_flag_name}'")
    print("  • Verification Flow : PENDING ➔ 1% ➔ 10% ➔ 50% ➔ APPROVAL ➔ 100% ➔ ROLLBACK (0%)")
    print("========================================================================")


if __name__ == "__main__":
    main()
