"""Deprecate charge-points node feature gating for the OCPP module."""

from django.db import migrations


CHARGE_POINTS_NODE_FEATURE_SLUG = "charge-points"
OCPP_MODULE_PATH = "/ocpp/"


def remove_charge_points_module_feature_gate(apps, schema_editor):
    """Remove the legacy charge-points gate from the seeded OCPP module."""

    Module = apps.get_model("modules", "Module")
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    db_alias = schema_editor.connection.alias

    module = Module.objects.using(db_alias).filter(path=OCPP_MODULE_PATH).first()
    if module is None:
        return

    feature = (
        NodeFeature.objects.using(db_alias)
        .filter(slug=CHARGE_POINTS_NODE_FEATURE_SLUG)
        .first()
    )
    if feature is not None:
        module.features.remove(feature)


def restore_charge_points_module_feature_gate(apps, schema_editor):
    """Restore the legacy charge-points gate for rollback compatibility."""

    Module = apps.get_model("modules", "Module")
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    db_alias = schema_editor.connection.alias

    module = Module.objects.using(db_alias).filter(path=OCPP_MODULE_PATH).first()
    if module is None:
        return

    feature, _ = NodeFeature.objects.using(db_alias).get_or_create(
        slug=CHARGE_POINTS_NODE_FEATURE_SLUG,
        defaults={
            "display": "Charge Points",
            "is_seed_data": True,
            "is_deleted": False,
        },
    )
    module.features.add(feature)


class Migration(migrations.Migration):
    dependencies = [
        ("modules", "0007_module_agent_notes"),
        ("nodes", "0037_merge_20260301_0001"),
        ("nodes", "0037_merge_20260301_1205"),
    ]

    operations = [
        migrations.RunPython(
            remove_charge_points_module_feature_gate,
            restore_charge_points_module_feature_gate,
        ),
    ]
