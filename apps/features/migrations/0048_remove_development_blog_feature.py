"""Remove the retired development blog feature metadata."""

from django.db import migrations


FEATURE_SLUG = "development-blog"
LEGACY_FEATURE_SLUG = "development-blog-suite"
FEATURE_SLUGS = (FEATURE_SLUG, LEGACY_FEATURE_SLUG)
FEATURE_BACKUP = {
    "display": "Development Blog",
    "summary": "Retired internal engineering blog suite feature.",
    "is_enabled": False,
}


def remove_feature(apps, schema_editor):
    """Soft-delete retired development blog feature rows without losing related records.

    Parameters:
        apps: Django migration app registry.
        schema_editor: Active migration schema editor.

    Returns:
        None.
    """

    Feature = apps.get_model("features", "Feature")
    db_alias = schema_editor.connection.alias
    feature_manager = getattr(Feature, "all_objects", Feature._base_manager).using(db_alias)
    feature_manager.filter(slug__in=FEATURE_SLUGS).update(is_deleted=True)


def restore_feature(apps, schema_editor):
    """Restore soft-deleted development blog feature rows for rollbacks.

    Parameters:
        apps: Django migration app registry.
        schema_editor: Active migration schema editor.

    Returns:
        None.
    """

    Feature = apps.get_model("features", "Feature")
    db_alias = schema_editor.connection.alias
    feature_manager = getattr(Feature, "all_objects", Feature._base_manager).using(db_alias)
    restored = feature_manager.filter(slug__in=FEATURE_SLUGS).update(is_deleted=False)
    if restored:
        return
    feature_manager.update_or_create(slug=FEATURE_SLUG, defaults={**FEATURE_BACKUP, "is_deleted": False})


class Migration(migrations.Migration):
    """Remove the retired development blog feature metadata."""

    dependencies = [
        ("features", "0047_remove_wikipedia_companion_suite_feature"),
    ]

    operations = [
        migrations.RunPython(remove_feature, restore_feature),
    ]
