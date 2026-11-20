import sys
from users.models import FFAdminUser
from organisations.models import Organisation, OrganisationRole, UserOrganisation
from projects.models import Project
from environments.models import Environment
from api_keys.models import MasterAPIKey
from experimentation.models import WarehouseConnection, WarehouseType, WarehouseConnectionStatus
from experimentation.views import verify_warehouse_connection

def setup():
    admin_email = "admin@example.com"
    admin_password = "AdminPassword123!"

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

    # 5. Master API Key (Admin API Key)
    # Check if key already exists
    master_keys = MasterAPIKey.objects.filter(organisation=org, revoked=False)
    if master_keys.exists():
        key_obj = master_keys.first()
        raw_key = "(existing key prefix: " + key_obj.prefix + ")"
    else:
        key_obj, raw_key = MasterAPIKey.objects.create_key(
            name="Guardian Admin Key",
            organisation=org,
            is_admin=True
        )
    print(f"Admin API Key Prefix: {key_obj.prefix}")
    print(f"Admin API Key: {raw_key}")

    # 6. Warehouse Connection (ClickHouse)
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

    print("\n=== SETUP SUMMARY ===")
    print(f"Dashboard URL: http://localhost:8000")
    print(f"Admin Login: {admin_email} / {admin_password}")
    print(f"Organisation: {org.name} (ID: {org.id})")
    print(f"Project: {project.name} (ID: {project.id})")
    print(f"Environment: {env.name} (ID: {env.id})")
    print(f"Environment Client Key: {env.api_key}")
    print(f"Admin API Key: {raw_key}")
    print(f"Warehouse Status: {wh_conn.status}")

if __name__ == "__main__":
    setup()
