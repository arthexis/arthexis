from django.db import migrations


def add_cpsim_service_feature(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    feature, created = NodeFeature.objects.get_or_create(
        slug="cpsim-service",
        defaults={
            "display": "CP Simulator Service",
            "is_seed_data": True,
            "is_deleted": False,
        },
    )
    if not created and feature.display != "CP Simulator Service":
        feature.display = "CP Simulator Service"
        feature.save(update_fields=["display"])


def remove_cpsim_service_feature(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.filter(slug="cpsim-service").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0024_upgrade_policies"),
    ]

    operations = [
        migrations.RunPython(add_cpsim_service_feature, remove_cpsim_service_feature),
    ]
