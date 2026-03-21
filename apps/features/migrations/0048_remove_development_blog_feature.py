"""Remove the retired development blog feature metadata."""

from django.db import migrations


FEATURE_SLUG = "development-blog"
FEATURE_BACKUP = {
    "display": "Development Blog",
    "summary": "Retired internal engineering blog suite feature.",
    "is_enabled": False,
}


def remove_feature(apps, schema_editor):
    """Delete the retired development blog feature entry."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=FEATURE_SLUG).delete()


def restore_feature(apps, schema_editor):
    """Restore the retired development blog feature entry for migration rollbacks."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.update_or_create(slug=FEATURE_SLUG, defaults=FEATURE_BACKUP)


class Migration(migrations.Migration):
    """Remove the retired development blog feature metadata."""

    dependencies = [
        ("features", "0047_remove_wikipedia_companion_suite_feature"),
    ]

    operations = [
        migrations.RunPython(remove_feature, restore_feature),
    ]
