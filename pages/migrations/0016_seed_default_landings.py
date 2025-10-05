from django.db import migrations


SATELLITE_PATH = "/ocpp/"
CONTROL_PATH = "/ocpp/rfid/"


def seed_role_landings(apps, schema_editor):
    RoleLanding = apps.get_model("pages", "RoleLanding")
    Landing = apps.get_model("pages", "Landing")
    NodeRole = apps.get_model("nodes", "NodeRole")

    role_paths = {
        "Satellite": SATELLITE_PATH,
        "Control": CONTROL_PATH,
    }

    for role_name, path in role_paths.items():
        try:
            role = NodeRole.objects.get(name=role_name)
        except NodeRole.DoesNotExist:
            continue
        landing = (
            Landing.objects.filter(module__node_role=role, path=path)
            .order_by("pk")
            .first()
        )
        if not landing:
            continue
        RoleLanding.objects.update_or_create(
            node_role=role,
            defaults={
                "landing": landing,
                "is_seed_data": True,
                "is_deleted": False,
            },
        )


def noop(apps, schema_editor):
    """No-op reverse migration."""


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0015_rolelanding"),
    ]

    operations = [
        migrations.RunPython(seed_role_landings, noop),
    ]
