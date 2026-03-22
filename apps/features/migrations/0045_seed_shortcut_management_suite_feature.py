"""Seed Shortcut Management suite feature."""

from django.db import migrations


FEATURE_SLUG = "shortcut-management"
NODE_FEATURE_SLUG = "shortcut-listener"


def seed_shortcut_management_suite_feature(apps, schema_editor):
    """Create or update the Shortcut Management suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    NodeFeature = apps.get_model("nodes", "NodeFeature")

    node_feature = NodeFeature.objects.filter(slug=NODE_FEATURE_SLUG).first()
    Feature.objects.update_or_create(
        slug=FEATURE_SLUG,
        defaults={
            "display": "Shortcut Management",
            "summary": (
                "Configure server and client keyboard shortcuts that execute recipes, "
                "support clipboard pattern routing, and optionally write output back to "
                "clipboard or keyboard targets."
            ),
            "is_enabled": True,
            "node_feature": node_feature,
            "service_requirements": (
                "Server shortcuts use the shortcut-listener node feature when available. "
                "Client shortcuts run in browser JavaScript and can evaluate clipboard "
                "patterns before selecting recipes."
            ),
            "service_views": [
                "/shortcuts/client/config/",
                "/shortcuts/client/execute/<shortcut_id>/",
            ],
            "code_locations": [
                "apps.shortcuts.models",
                "apps.shortcuts.views",
                "apps.shortcuts.node_features",
            ],
            "source": "mainstream",
        },
    )


def unseed_shortcut_management_suite_feature(apps, schema_editor):
    """Delete Shortcut Management suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0044_merge_20260309_1846"),
        ("nodes", "0043_add_shortcut_listener_feature"),
    ]

    operations = [
        migrations.RunPython(
            seed_shortcut_management_suite_feature,
            reverse_code=unseed_shortcut_management_suite_feature,
        ),
    ]
