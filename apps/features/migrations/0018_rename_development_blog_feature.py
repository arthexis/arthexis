"""Rename the development blog feature seed to its non-suite name."""

from django.db import migrations


OLD_SLUG = "development-blog-suite"
NEW_SLUG = "development-blog"
OLD_DISPLAY = "Development Blog suite feature"
NEW_DISPLAY = "Development Blog"


def rename_development_blog_feature(apps, schema_editor):
    """Rename the seeded development blog feature slug and display.

    Parameters:
        apps: Django migration app registry.
        schema_editor: Active migration schema editor.

    Returns:
        None.
    """

    Feature = apps.get_model("features", "Feature")
    FeatureTest = apps.get_model("features", "FeatureTest")
    FeatureNote = apps.get_model("features", "FeatureNote")
    db_alias = schema_editor.connection.alias

    feature_manager = getattr(Feature, "all_objects", Feature._base_manager).using(db_alias)
    feature_test_manager = getattr(FeatureTest, "all_objects", FeatureTest._base_manager).using(db_alias)
    feature_note_manager = getattr(FeatureNote, "all_objects", FeatureNote._base_manager).using(db_alias)

    old_feature = feature_manager.filter(slug=OLD_SLUG).first()
    new_feature = feature_manager.filter(slug=NEW_SLUG).first()

    if old_feature and new_feature and old_feature.pk != new_feature.pk:
        feature_test_manager.filter(feature=old_feature).update(feature=new_feature)
        feature_note_manager.filter(feature=old_feature).update(feature=new_feature)
        feature_manager.filter(pk=old_feature.pk).delete()
        old_feature = None

    target_feature = new_feature or old_feature
    if not target_feature:
        return

    target_feature.slug = NEW_SLUG
    if target_feature.display == OLD_DISPLAY:
        target_feature.display = NEW_DISPLAY
    target_feature.save(update_fields=["slug", "display"])


def rollback_development_blog_feature(apps, schema_editor):
    """Restore the seeded development blog feature slug and display.

    Parameters:
        apps: Django migration app registry.
        schema_editor: Active migration schema editor.

    Returns:
        None.
    """

    Feature = apps.get_model("features", "Feature")
    FeatureTest = apps.get_model("features", "FeatureTest")
    FeatureNote = apps.get_model("features", "FeatureNote")
    db_alias = schema_editor.connection.alias

    feature_manager = getattr(Feature, "all_objects", Feature._base_manager).using(db_alias)
    feature_test_manager = getattr(FeatureTest, "all_objects", FeatureTest._base_manager).using(db_alias)
    feature_note_manager = getattr(FeatureNote, "all_objects", FeatureNote._base_manager).using(db_alias)

    old_feature = feature_manager.filter(slug=OLD_SLUG).first()
    new_feature = feature_manager.filter(slug=NEW_SLUG).first()

    if old_feature and new_feature and old_feature.pk != new_feature.pk:
        feature_test_manager.filter(feature=new_feature).update(feature=old_feature)
        feature_note_manager.filter(feature=new_feature).update(feature=old_feature)
        feature_manager.filter(pk=new_feature.pk).delete()
        new_feature = None

    target_feature = old_feature or new_feature
    if not target_feature:
        return

    target_feature.slug = OLD_SLUG
    if target_feature.display == NEW_DISPLAY:
        target_feature.display = OLD_DISPLAY
    target_feature.save(update_fields=["slug", "display"])


class Migration(migrations.Migration):
    """Apply the development blog feature rename migration."""

    dependencies = [
        ("features", "0017_merge_20260224_2131"),
    ]

    operations = [
        migrations.RunPython(rename_development_blog_feature, rollback_development_blog_feature),
    ]
