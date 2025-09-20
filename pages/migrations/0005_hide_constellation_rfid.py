from django.db import migrations


RFID_PATH = "/ocpp/rfid/"


def hide_constellation_rfid(apps, schema_editor):
    Module = apps.get_model("pages", "Module")
    Landing = apps.get_model("pages", "Landing")
    NodeRole = apps.get_model("nodes", "NodeRole")

    role = NodeRole.objects.filter(name="Constellation").first()
    if not role:
        return

    Module.objects.filter(node_role=role, path=RFID_PATH).update(is_deleted=True)
    Landing.objects.filter(module__node_role=role, path=RFID_PATH).update(
        is_deleted=True
    )


def restore_constellation_rfid(apps, schema_editor):
    Module = apps.get_model("pages", "Module")
    Landing = apps.get_model("pages", "Landing")
    NodeRole = apps.get_model("nodes", "NodeRole")

    role = NodeRole.objects.filter(name="Constellation").first()
    if not role:
        return

    Module.objects.filter(node_role=role, path=RFID_PATH).update(is_deleted=False)
    Landing.objects.filter(module__node_role=role, path=RFID_PATH).update(
        is_deleted=False
    )


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0004_viewhistory"),
    ]

    operations = [
        migrations.RunPython(hide_constellation_rfid, restore_constellation_rfid),
    ]
