"""Seed the Screenshot Capture suite feature."""

from django.db import migrations


FEATURE_SLUG = "screenshot-capture"
NODE_FEATURE_SLUG = "screenshot-poll"


def seed_feature(apps, schema_editor):
    """Create or update the Screenshot Capture suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    node_feature = NodeFeature.objects.filter(slug=NODE_FEATURE_SLUG).first()
    Feature.objects.update_or_create(
        slug=FEATURE_SLUG,
        defaults={
            "display": "Screenshot Capture",
            "source": "mainstream",
            "summary": (
                "Controls whether runtime screenshot capture actions are available "
                "for admin and background node polling."
            ),
            "is_enabled": True,
            "node_feature": node_feature,
            "admin_requirements": (
                "NodeFeature admin should block manual capture when runtime prerequisites fail."
            ),
            "public_requirements": "No public UI impact.",
            "service_requirements": (
                "Playwright package and Chromium runtime must be available in the application environment."
            ),
            "admin_views": ["admin:nodes_nodefeature_take_screenshot"],
            "public_views": [],
            "service_views": ["apps.nodes.tasks.capture_node_screenshot"],
            "code_locations": [
                "apps/nodes/feature_checks.py",
                "apps/nodes/admin/node_feature_admin.py",
                "apps/content/utils.py",
            ],
            "protocol_coverage": {},
        },
    )


def unseed_feature(apps, schema_editor):
    """Remove the Screenshot Capture suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0019_merge_20260225_1819"),
        ("nodes", "0032_retire_charge_points_node_feature"),
    ]

    operations = [
        migrations.RunPython(seed_feature, unseed_feature),
    ]
