"""Retire the legacy charge-points node feature gate for OCPP admission."""

from django.db import migrations

LEGACY_CHARGE_POINTS_SLUG = "charge-points"


def remove_legacy_charge_point_node_feature(apps, schema_editor):
    """Delete the retired charge-points node feature and its assignments."""

    del schema_editor
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.filter(slug=LEGACY_CHARGE_POINTS_SLUG).delete()


def restore_legacy_charge_point_node_feature(apps, schema_editor):
    """Recreate the retired charge-points node feature for migration rollback."""

    del schema_editor
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.update_or_create(
        slug=LEGACY_CHARGE_POINTS_SLUG,
        defaults={
            "display": "Charge Points",
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0031_merge_20260226_1839"),
    ]

    operations = [
        migrations.RunPython(
            remove_legacy_charge_point_node_feature,
            restore_legacy_charge_point_node_feature,
        ),
    ]
