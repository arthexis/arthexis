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
                "for the local application instance."
            ),
            "is_enabled": True,
            "node_feature": node_feature,
            "admin_requirements": (
                "Expose a Take Screenshot action in admin only when runtime "
                "prerequisites are satisfied."
            ),
            "public_requirements": "No direct public pages.",
            "service_requirements": (
                "Requires Playwright importability and Chromium runtime binaries."
            ),
            "admin_views": ["admin:nodes_nodefeature_take_screenshot"],
            "public_views": [],
            "service_views": [],
            "code_locations": [
                "apps/nodes/admin/node_feature_admin.py",
                "apps/nodes/feature_checks.py",
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
        ("nodes", "0031_merge_20260226_1839"),
    ]

    operations = [
        migrations.RunPython(seed_feature, unseed_feature),
    ]
