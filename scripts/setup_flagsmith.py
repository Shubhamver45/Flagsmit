import os
import sys
import time

def setup():
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@example.com")
    admin_password = os.environ.get("ADMIN_PASSWORD", "changeme_flagsmith_admin_pass")

    from users.models import FFAdminUser
    from organisations.models import Organisation, OrganisationRole, UserOrganisation
    from projects.models import Project
    from environments.models import Environment
    from api_keys.models import MasterAPIKey
    from features.models import Feature, FeatureState
    from experimentation.models import WarehouseConnection, WarehouseType, WarehouseConnectionStatus
    from experimentation.views import verify_warehouse_connection

    # 1. Admin User
    user, created = FFAdminUser.objects.get_or_create(
        email=admin_email,
        defaults={
            "first_name": "Admin",
            "last_name": "Guardian",
            "is_staff": True,
            "is_superuser": True,
        }
    )
    user.set_password(admin_password)
    user.is_staff = True
    user.is_superuser = True
    user.save()
    print(f"User: {user.email} (created: {created})")

    # 2. Organisation
    org, created = Organisation.objects.get_or_create(
        name="Guardian Org",
    )
    UserOrganisation.objects.get_or_create(
        user=user,
        organisation=org,
        defaults={"role": OrganisationRole.ADMIN.name}
    )
    print(f"Organisation: {org.name} [ID={org.id}]")

    # 3. Project
    project, created = Project.objects.get_or_create(
        name="Guardian Project",
        organisation=org
    )
    print(f"Project: {project.name} [ID={project.id}]")

    # 4. Environment
    env, created = Environment.objects.get_or_create(
        name="Development",
        project=project
    )
    print(f"Environment: {env.name} [ID={env.id}, Key={env.api_key}]")

    # 5. Features / Flags
    feature, feat_created = Feature.objects.get_or_create(
        project=project,
        name="new_checkout",
        defaults={
            "description": "New checkout flow gated rollout flag",
            "default_enabled": False,
            "type": "STANDARD",
        }
    )
    # Ensure default environment feature state exists
    fs = FeatureState.objects.filter(
        feature=feature,
        environment=env,
        feature_segment__isnull=True,
        identity__isnull=True,
    ).first()
    if not fs:
        fs = FeatureState.objects.create(feature=feature, environment=env, enabled=False)
    print(f"Feature: {feature.name} [ID={feature.id}, Enabled={fs.enabled}] (created: {feat_created})")

    # 6. Master Admin API Key
    # Remove existing Guardian Admin Keys to ensure we generate a fresh, known raw key
    MasterAPIKey.objects.filter(organisation=org, name="Guardian Admin Key").delete()
    key_obj, raw_key = MasterAPIKey.objects.create_key(
        name="Guardian Admin Key",
        organisation=org,
        is_admin=True
    )
    print(f"Admin API Key Prefix: {key_obj.prefix}")
    print(f"Admin API Key: {raw_key}")

    # 7. Warehouse Connection (Flagsmith Database for SDK event collection)
    wh_conn, created = WarehouseConnection.objects.get_or_create(
        environment=env,
        defaults={
            "name": f"Flagsmith Database - {env.name}",
            "warehouse_type": WarehouseType.POSTGRES.value,
            "config": {"source": "flagsmith_database"},
            "credentials": {},
            "status": WarehouseConnectionStatus.CREATED,
        }
    )
    wh_conn.warehouse_type = WarehouseType.POSTGRES.value
    wh_conn.config = {"source": "flagsmith_database"}
    wh_conn.credentials = {}
    wh_conn.save()

    print(f"Verifying Flagsmith Database Warehouse connection for environment '{env.name}'...")
    verify_warehouse_connection(wh_conn, persist=True)
    wh_conn.refresh_from_db()
    print(f"Warehouse Connection Status: {wh_conn.status} (detail: {wh_conn.status_detail})")

    if wh_conn.status != WarehouseConnectionStatus.CONNECTED:
        print(f"ERROR: Warehouse connection is not CONNECTED: {wh_conn.status_detail}", file=sys.stderr)
        sys.exit(1)

    # 8. Experimentation Setup: Feature, Metric, and Experiment (Real Events Only - No Seeded Results)
    from features.multivariate.models import MultivariateFeatureOption
    from experimentation.models import (
        Experiment,
        Metric,
        ExperimentMetric,
        ExpectedDirection,
        ExperimentStatus,
    )
    from experimentation.services import apply_experiment_rollout, transition_experiment_status
    from experimentation.dataclasses import RolloutSpec, Variant
    from core.dataclasses import AuthorData

    # A. Multivariate Feature for Experiment (separate from rollout flag 'new_checkout')
    exp_feature, _ = Feature.objects.get_or_create(
        project=project,
        name="checkout_experiment",
        defaults={
            "description": "Multivariate feature flag for checkout A/B experiment",
            "type": "MULTIVARIATE",
            "default_enabled": True,
            "initial_value": "control",
        }
    )
    mv_opt, _ = MultivariateFeatureOption.objects.get_or_create(
        feature=exp_feature,
        key="variation",
        defaults={
            "type": "unicode",
            "string_value": "variation",
            "default_percentage_allocation": 50.0,
        }
    )
    mv_opt.key = "variation"
    mv_opt.default_percentage_allocation = 50.0
    mv_opt.save()

    fs_exp = FeatureState.objects.filter(
        feature=exp_feature,
        environment=env,
        feature_segment__isnull=True,
        identity__isnull=True,
    ).first()
    if not fs_exp:
        fs_exp = FeatureState.objects.create(feature=exp_feature, environment=env, enabled=True)
    mv_fs, _ = fs_exp.multivariate_feature_state_values.get_or_create(
        multivariate_feature_option=mv_opt,
        defaults={"percentage_allocation": 50.0}
    )
    mv_fs.percentage_allocation = 50.0
    mv_fs.save()

    # B. Conversion Metric on event 'checkout_completed'
    metric, _ = Metric.objects.get_or_create(
        environment=env,
        name="Checkout Completed",
        defaults={
            "description": "Conversion metric tracking completed checkout purchases",
            "aggregation": "count",
            "direction": "up",
            "definition": {"version": 1, "event": "checkout_completed"},
        }
    )

    # C. Experiment: Checkout A/B Experiment
    # Reset existing experiment and historical events for clean, reproducible demonstration
    from experimentation.models import ExperimentExposures, ExperimentResults, SDKExperimentationEvent
    SDKExperimentationEvent.objects.filter(environment_key=env.api_key).delete()
    for existing_exp in Experiment.objects.filter(feature=exp_feature, environment=env):
        ExperimentResults.objects.filter(experiment=existing_exp).delete()
        ExperimentExposures.objects.filter(experiment=existing_exp).delete()
        existing_exp.delete()

    exp = Experiment.objects.create(
        feature=exp_feature,
        name="Checkout A/B Experiment",
        hypothesis="New checkout flow increases completed checkouts without increasing errors",
        environment=env,
    )
    ExperimentMetric.objects.create(
        experiment=exp,
        metric=metric,
        expected_direction=ExpectedDirection.INCREASE.value,
    )
    apply_experiment_rollout(
        exp,
        RolloutSpec(
            enabled=True,
            rollout_percentage=100.0,
            feature_state_value="control",
            value_type="string",
            variants=[Variant(id=mv_opt.id, weight=50.0)],
            author=AuthorData.from_user(user),
        ),
    )
    transition_experiment_status(exp, ExperimentStatus.RUNNING.value, user)
    # Ensure expected variant shares (for SRM calculation) evaluate to 50% variation, 50% control
    for fs in FeatureState.objects.filter(feature=exp_feature, environment=env):
        fs.multivariate_feature_state_values.filter(multivariate_feature_option=mv_opt).update(percentage_allocation=50.0)
    print(f"Configured & Started Experiment: '{exp.name}' [ID={exp.id}, status={exp.status}, segment={exp.rollout_segment_id}]")

    # 9. Generate .env file contents
    dotenv_content = f"""# Flagsmith API Configuration (Auto-generated by scripts/setup_flagsmith.py)
FLAGSMITH_URL=http://localhost:8000/api/v1
FLAGSMITH_ADMIN_API_KEY={raw_key}
FLAGSMITH_ENVIRONMENT_KEY={env.api_key}
FLAGSMITH_ORG_ID={org.id}
FLAGSMITH_PROJECT_ID={project.id}
FLAGSMITH_ENVIRONMENT_ID={env.id}

# Flagsmith Experimentation Recalculation (Optional, DEMO_FORCE_RECALC=1 for fast demo testing)
# DEMO_FORCE_RECALC=1

# Initial Flagsmith Admin Credentials (for setup)
ADMIN_EMAIL={admin_email}
ADMIN_PASSWORD={admin_password}

# Guardian API Authentication (Optional for local dev, recommended for production)
# GUARDIAN_API_TOKEN=

# Demo App Authentication (Optional, protects /toggle-bug and /reset-metrics)
# DEMO_API_TOKEN=
"""

    # Write to container local .env if possible
    try:
        with open("/app/.env", "w", encoding="utf-8") as f:
            f.write(dotenv_content)
    except Exception:
        pass

    print("\n=== SETUP SUMMARY ===")
    print(f"Dashboard URL: http://localhost:8000")
    print(f"Admin Login: {admin_email} / {admin_password}")
    print(f"Organisation: {org.name} (ID: {org.id})")
    print(f"Project: {project.name} (ID: {project.id})")
    print(f"Environment: {env.name} (ID: {env.id})")
    print(f"Environment Client Key: {env.api_key}")
    print(f"Admin API Key: {raw_key}")
    print(f"Warehouse Status: {wh_conn.status}")

    # Delimited block for host extraction
    print("\n=== DOTENV_START ===")
    print(dotenv_content.strip())
    print("=== DOTENV_END ===\n")


def host_run():
    """Wrapper executed when setup_flagsmith.py is called from host."""
    import subprocess
    from pathlib import Path

    root_dir = Path(__file__).resolve().parent.parent
    script_path = root_dir / "scripts" / "setup_flagsmith.py"
    env_file = root_dir / ".env"

    print("==> Copying setup script into Flagsmith container...")
    subprocess.run(["docker", "compose", "cp", str(script_path), "flagsmith:/app/setup_flagsmith.py"], check=True)

    print("==> Executing setup inside Flagsmith...")
    try:
        proc = subprocess.run(
            ["docker", "compose", "exec", "-T", "flagsmith", "python", "manage.py", "shell", "-c", "exec(open('/app/setup_flagsmith.py').read()); setup()"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print("Setup inside Flagsmith failed!", file=sys.stderr)
        if e.stdout:
            print("--- STDOUT ---", file=sys.stderr)
            print(e.stdout, file=sys.stderr)
        if e.stderr:
            print("--- STDERR ---", file=sys.stderr)
            print(e.stderr, file=sys.stderr)
        raise
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)

    if "=== DOTENV_START ===" in proc.stdout:
        dotenv = proc.stdout.split("=== DOTENV_START ===")[1].split("=== DOTENV_END ===")[0].strip()
        # Preserve SLACK_WEBHOOK_URL and other custom keys if already defined
        existing_slack = ""
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("SLACK_WEBHOOK_URL="):
                    existing_slack = line.strip()
        if existing_slack:
            dotenv += f"\n{existing_slack}\n"
        else:
            dotenv += "\n# Webhooks (Optional)\nSLACK_WEBHOOK_URL=\n"
        with open(env_file, "w", encoding="utf-8") as f:
            f.write(dotenv + "\n")
        print(f"✔ Automatically configured and wrote {env_file}!")


if __name__ == "__main__":
    try:
        import django
        setup()
    except ImportError:
        host_run()
