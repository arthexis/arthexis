"""Remove the retired Development Blog suite feature."""

from django.db import migrations


FEATURE_SLUG = "development-blog"
FEATURE_DISPLAY = "Development Blog"


def remove_development_blog_suite_feature(apps, schema_editor):
    """Disable and remove the retired Development Blog suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    feature_manager = getattr(Feature, "all_objects", Feature._base_manager)
    feature_manager.filter(slug=FEATURE_SLUG).update(is_enabled=False)
    feature_manager.filter(slug=FEATURE_SLUG).delete()


def restore_development_blog_suite_feature(apps, schema_editor):
    """Recreate the Development Blog feature on rollback."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.update_or_create(
        slug=FEATURE_SLUG,
        defaults={
            "display": FEATURE_DISPLAY,
            "is_enabled": True,
            "summary": (
                "Legacy engineering blog feature retained only for migration rollback. "
                "Public engineering updates now live on the changelog page."
            ),
            "public_requirements": "Public engineering updates are served from the changelog page.",
            "public_views": ["pages:changelog"],
            "service_views": [],
            "code_locations": ["apps/sites/urls.py"],
            "metadata": {"replacement_public_view": "pages:changelog"},
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0046_seed_playwright_automation_suite_feature"),
    ]

    operations = [
        migrations.RunPython(
            remove_development_blog_suite_feature,
            reverse_code=restore_development_blog_suite_feature,
        ),
    ]
