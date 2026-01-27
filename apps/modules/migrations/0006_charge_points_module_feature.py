from django.db import migrations


def add_charge_points_module_feature(apps, schema_editor):
    Module = apps.get_model("modules", "Module")
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    module = Module.objects.filter(path="/ocpp/").first()
    if not module:
        return
    feature, _ = NodeFeature.objects.get_or_create(
        slug="charge-points",
        defaults={
            "display": "Charge Points",
            "is_seed_data": True,
            "is_deleted": False,
        },
    )
    module.features.add(feature)


def remove_charge_points_module_feature(apps, schema_editor):
    Module = apps.get_model("modules", "Module")
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    module = Module.objects.filter(path="/ocpp/").first()
    if not module:
        return
    feature = NodeFeature.objects.filter(slug="charge-points").first()
    if feature:
        module.features.remove(feature)


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0021_add_charge_points_feature"),
        ("modules", "0005_module_features"),
    ]

    operations = [
        migrations.RunPython(
            add_charge_points_module_feature,
            remove_charge_points_module_feature,
        ),
    ]
