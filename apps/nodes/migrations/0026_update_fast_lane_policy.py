from django.db import migrations


def update_fast_lane_policy(apps, schema_editor):
    UpgradePolicy = apps.get_model("nodes", "UpgradePolicy")
    UpgradePolicy.objects.update_or_create(
        name="Fast Lane",
        defaults={
            "description": "Unstable channel with hourly checks.",
            "channel": "unstable",
            "interval_minutes": 60,
            "requires_canaries": False,
            "requires_pypi_packages": False,
            "is_seed_data": True,
            "is_deleted": False,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0025_add_cpsim_service_feature"),
    ]

    operations = [
        migrations.RunPython(
            update_fast_lane_policy,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
