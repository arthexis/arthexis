from django.db import migrations


SYSTEMD_MANAGER_SLUG = "systemd-manager"
SYSTEMD_MANAGER_DISPLAY = "Systemd Manager"


def add_systemd_manager_feature(apps, schema_editor):
    """Seed the systemd-manager node feature used for service capability gating."""

    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.update_or_create(
        slug=SYSTEMD_MANAGER_SLUG,
        defaults={
            "display": SYSTEMD_MANAGER_DISPLAY,
            "is_seed_data": True,
            "is_deleted": False,
        },
    )


def remove_systemd_manager_feature(apps, schema_editor):
    """Remove the seeded systemd-manager node feature."""

    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.filter(slug=SYSTEMD_MANAGER_SLUG).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0038_merge_20260301_1900"),
    ]

    operations = [
        migrations.RunPython(
            add_systemd_manager_feature,
            remove_systemd_manager_feature,
        ),
    ]
