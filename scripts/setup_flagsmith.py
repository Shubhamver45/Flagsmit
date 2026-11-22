import os
import sys
import time

def setup():
    admin_email = "admin@example.com"
    admin_password = "AdminPassword123!"

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

    # 7. Warehouse Connection (ClickHouse)
    wh_conn, created = WarehouseConnection.objects.get_or_create(
        environment=env,
        warehouse_type=WarehouseType.CLICKHOUSE.value,
        defaults={
            "name": f"ClickHouse - {env.name}",
            "config": {
                "host": "clickhouse",
                "port": 8123,
                "database": "flagsmith_exp",
                "username": "flagsmith",
                "secure": False,
            },
            "credentials": {"password": "password"},
            "status": WarehouseConnectionStatus.CREATED,
        }
    )
    if not created:
        wh_conn.config = {
            "host": "clickhouse",
            "port": 8123,
            "database": "flagsmith_exp",
            "username": "flagsmith",
            "secure": False,
        }
        wh_conn.credentials = {"password": "password"}
        wh_conn.save()

    print(f"Verifying ClickHouse Warehouse connection for environment '{env.name}'...")
    verify_warehouse_connection(wh_conn, persist=True)
    wh_conn.refresh_from_db()
    print(f"Warehouse Connection Status: {wh_conn.status} (detail: {wh_conn.status_detail})")

    if wh_conn.status != WarehouseConnectionStatus.CONNECTED:
        print(f"ERROR: Warehouse connection is not CONNECTED: {wh_conn.status_detail}", file=sys.stderr)
        sys.exit(1)

    # 8. Flagsmith Experimentation: Checkout A/B Experiment & Results
    from experimentation.models import Experiment, ExperimentResults
    from django.utils import timezone

    exp, exp_created = Experiment.objects.get_or_create(
        project=project,
        name="Checkout A/B Experiment",
        defaults={
            "feature": feature,
            "description": "Checkout flow conversion rate experiment",
            "status": "RUNNING",
            "started_at": timezone.now(),
        }
    )
    exp.status = "RUNNING"
    if not exp.started_at:
        exp.started_at = timezone.now()
    exp.save()

    sample_results_payload = {
        "metrics": [
            {
                "metric_id": 1,
                "variants": {
                    "control": {"n": 1200, "sum": 120.0, "sum_squares": 120.0},
                    "new_checkout": {"n": 1195, "sum": 145.0, "sum_squares": 145.0},
                },
                "inference": {
                    "new_checkout": {
                        "lift": 0.208,
                        "ci_low": 0.015,
                        "ci_high": 0.41,
                        "chance_to_win": 0.942,
                    }
                },
                "conversions_timeseries": [],
            }
        ],
        "srm_p_value": 0.4821,
        "exposures_timeseries": [],
    }

    res_obj, res_created = ExperimentResults.objects.get_or_create(
        experiment=exp,
        environment=env,
        defaults={
            "as_of": timezone.now(),
            "payload": sample_results_payload,
            "is_final": False,
        }
    )
    if not res_created and not res_obj.payload:
        res_obj.payload = sample_results_payload
        res_obj.as_of = timezone.now()
        res_obj.save()

    print(f"Experiment: {exp.name} [ID={exp.id}, Status={exp.status}] (created: {exp_created})")
    print(f"Experiment Results: [ID={res_obj.id}, as_of={res_obj.as_of}] (created: {res_created})")

    # 9. Generate .env file contents
    dotenv_content = f"""# Flagsmith API Configuration (Auto-generated by scripts/setup_flagsmith.py)
FLAGSMITH_URL=http://localhost:8000/api/v1
FLAGSMITH_ADMIN_API_KEY={raw_key}
FLAGSMITH_ENVIRONMENT_KEY={env.api_key}
FLAGSMITH_ORG_ID={org.id}
FLAGSMITH_PROJECT_ID={project.id}
FLAGSMITH_ENVIRONMENT_ID={env.id}

# ClickHouse Experimentation Warehouse
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=8123
CLICKHOUSE_USER=flagsmith
CLICKHOUSE_PASSWORD=password
CLICKHOUSE_DB=flagsmith_exp

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
    proc = subprocess.run(
        ["docker", "compose", "exec", "-T", "flagsmith", "python", "manage.py", "shell", "-c", "exec(open('/app/setup_flagsmith.py').read()); setup()"],
        capture_output=True,
        text=True,
        check=True,
    )
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)

    if "=== DOTENV_START ===" in proc.stdout:
        dotenv = proc.stdout.split("=== DOTENV_START ===")[1].split("=== DOTENV_END ===")[0].strip()
        with open(env_file, "w", encoding="utf-8") as f:
            f.write(dotenv + "\n")
        print(f"✔ Automatically configured and wrote {env_file}!")


if __name__ == "__main__":
    try:
        import django
        setup()
    except ImportError:
        host_run()
