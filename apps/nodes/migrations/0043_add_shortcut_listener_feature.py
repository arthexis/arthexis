"""Seed shortcut-listener node feature."""

from django.db import migrations


NODE_FEATURE_SLUG = "shortcut-listener"


def seed_shortcut_listener_feature(apps, schema_editor):
    """Create or update the shortcut-listener node feature."""

    del schema_editor
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.update_or_create(
        slug=NODE_FEATURE_SLUG,
        defaults={
            "display": "Shortcut Listener",
            "footprint": "light",
        },
    )


def unseed_shortcut_listener_feature(apps, schema_editor):
    """Delete shortcut-listener feature on reverse migration."""

    del schema_editor
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.filter(slug=NODE_FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0042_update_terminal_satellite_acronyms"),
    ]

    operations = [
        migrations.RunPython(
            seed_shortcut_listener_feature,
            reverse_code=unseed_shortcut_listener_feature,
        ),
    ]
