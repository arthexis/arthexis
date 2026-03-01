"""Drop screenshot-poll node assignments now that control is suite-scoped."""

import logging

from django.db import migrations


FEATURE_SLUG = "screenshot-poll"

logger = logging.getLogger(__name__)


def remove_screenshot_assignments(apps, schema_editor):
    """Delete per-node screenshot assignments that are no longer used."""

    del schema_editor
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeatureAssignment = apps.get_model("nodes", "NodeFeatureAssignment")
    feature = NodeFeature.objects.filter(slug=FEATURE_SLUG).first()
    if feature is None:
        return
    queryset = NodeFeatureAssignment.objects.filter(feature=feature)
    deleted_count = queryset.count()
    queryset.delete()
    logger.info(
        "Removed %s legacy %s assignment(s).",
        deleted_count,
        FEATURE_SLUG,
    )


def restore_screenshot_assignments(apps, schema_editor):
    """Reversal no-op because deleted assignments cannot be reconstructed safely."""

    del apps, schema_editor


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0032_retire_charge_points_node_feature"),
    ]

    operations = [
        migrations.RunPython(
            remove_screenshot_assignments,
            restore_screenshot_assignments,
        ),
    ]
