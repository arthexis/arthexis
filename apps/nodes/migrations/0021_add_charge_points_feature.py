from django.db import migrations


def add_charge_points_feature(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeRole = apps.get_model("nodes", "NodeRole")
    feature, created = NodeFeature.objects.get_or_create(
        slug="charge-points",
        defaults={
            "display": "Charge Points",
            "is_seed_data": True,
            "is_deleted": False,
        },
    )
    if not created and feature.display != "Charge Points":
        feature.display = "Charge Points"
        feature.save(update_fields=["display"])
    role = NodeRole.objects.filter(name="Satellite").first()
    if role:
        feature.roles.add(role)


def remove_charge_points_feature(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.filter(slug="charge-points").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0020_node_upgrade_canaries"),
    ]

    operations = [
        migrations.RunPython(add_charge_points_feature, remove_charge_points_feature),
    ]
