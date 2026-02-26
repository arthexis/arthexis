from django.db import migrations


def set_policy_requirements(apps, schema_editor):
    """Enable stricter defaults for Stable and Fast Lane upgrade policies."""

    del schema_editor
    UpgradePolicy = apps.get_model("nodes", "UpgradePolicy")
    UpgradePolicy.objects.filter(name="Stable").update(requires_pypi_packages=True)
    UpgradePolicy.objects.filter(name="Fast Lane").update(requires_canaries=True)


def unset_policy_requirements(apps, schema_editor):
    """Restore previous Stable and Fast Lane requirement defaults."""

    del schema_editor
    UpgradePolicy = apps.get_model("nodes", "UpgradePolicy")
    UpgradePolicy.objects.filter(name="Stable").update(requires_pypi_packages=False)
    UpgradePolicy.objects.filter(name="Fast Lane").update(requires_canaries=False)


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0028_upgradepolicy_is_active"),
    ]

    operations = [
        migrations.RunPython(set_policy_requirements, unset_policy_requirements),
    ]
