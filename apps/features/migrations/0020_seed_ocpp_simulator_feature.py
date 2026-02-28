"""Seed the OCPP Simulator suite feature from the existing node feature."""

from django.db import migrations


FEATURE_SLUG = "cpsim-service"


def seed_ocpp_simulator_feature(apps, schema_editor):
    """Create or update the OCPP Simulator suite feature."""

    Feature = apps.get_model("features", "Feature")
    NodeFeature = apps.get_model("nodes", "NodeFeature")

    node_feature = NodeFeature.objects.filter(slug=FEATURE_SLUG).first()
    is_enabled = False
    if node_feature is not None:
        local_assignment_exists = node_feature.node_assignments.exists()
        is_enabled = bool(local_assignment_exists)

    Feature.objects.update_or_create(
        slug=FEATURE_SLUG,
        defaults={
            "display": "OCPP Simulator",
            "summary": "Controls whether CPSIM service requests are dispatched.",
            "source": "mainstream",
            "is_enabled": is_enabled,
            "node_feature": node_feature,
        },
    )


def unseed_ocpp_simulator_feature(apps, schema_editor):
    """Remove the seeded OCPP Simulator suite feature."""

    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0031_merge_20260226_1839"),
        ("features", "0019_merge_20260225_1819"),
    ]

    operations = [
        migrations.RunPython(
            seed_ocpp_simulator_feature,
            unseed_ocpp_simulator_feature,
        ),
    ]
