"""Seed llvm-sigils node feature."""

from django.db import migrations


NODE_FEATURE_SLUG = "llvm-sigils"


def seed_llvm_sigils_feature(apps, schema_editor):
    """Create or update the llvm-sigils node feature."""

    del schema_editor
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.update_or_create(
        slug=NODE_FEATURE_SLUG,
        defaults={
            "display": "LLVM Sigils",
            "footprint": "light",
        },
    )


def unseed_llvm_sigils_feature(apps, schema_editor):
    """Delete llvm-sigils feature on reverse migration."""

    del schema_editor
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.filter(slug=NODE_FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0043_add_shortcut_listener_feature"),
    ]

    operations = [
        migrations.RunPython(
            seed_llvm_sigils_feature,
            reverse_code=unseed_llvm_sigils_feature,
        ),
    ]
