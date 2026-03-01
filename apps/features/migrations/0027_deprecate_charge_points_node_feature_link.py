"""Decouple OCPP suite features from the legacy charge-points node feature."""

from django.db import migrations


CHARGE_POINTS_NODE_FEATURE_SLUG = "charge-points"
OCPP_SUITE_FEATURE_SLUGS = (
    "ocpp-16-charge-point",
    "ocpp-201-charge-point",
    "ocpp-21-charge-point",
)


def drop_legacy_node_feature_binding(apps, schema_editor):
    """Clear legacy node feature bindings from seeded OCPP suite features."""

    Feature = apps.get_model("features", "Feature")
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    db_alias = schema_editor.connection.alias

    legacy_feature = (
        NodeFeature.objects.using(db_alias)
        .filter(slug=CHARGE_POINTS_NODE_FEATURE_SLUG)
        .first()
    )
    if legacy_feature is None:
        return

    Feature.objects.using(db_alias).filter(
        slug__in=OCPP_SUITE_FEATURE_SLUGS,
        node_feature=legacy_feature,
    ).update(node_feature=None)


def restore_legacy_node_feature_binding(apps, schema_editor):
    """Rebind OCPP suite features to the charge-points node feature on rollback."""

    Feature = apps.get_model("features", "Feature")
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    db_alias = schema_editor.connection.alias

    legacy_feature, _ = NodeFeature.objects.using(db_alias).get_or_create(
        slug=CHARGE_POINTS_NODE_FEATURE_SLUG,
        defaults={
            "display": "Charge Points",
            "is_seed_data": True,
            "is_deleted": False,
        },
    )
    Feature.objects.using(db_alias).filter(slug__in=OCPP_SUITE_FEATURE_SLUGS).update(
        node_feature=legacy_feature
    )


class Migration(migrations.Migration):
    dependencies = [
        ("features", "0026_merge_20260301_1339"),
        ("nodes", "0037_merge_20260301_0001"),
        ("nodes", "0037_merge_20260301_1205"),
    ]

    operations = [
        migrations.RunPython(
            drop_legacy_node_feature_binding,
            restore_legacy_node_feature_binding,
        ),
    ]
