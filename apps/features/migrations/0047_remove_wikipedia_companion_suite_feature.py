"""Remove the retired Wikipedia Companion suite feature."""

from django.db import migrations


FEATURE_SLUG = "wikipedia-companion"


def remove_wikipedia_companion_suite_feature(apps, schema_editor):
    """Delete legacy Wikipedia Companion feature rows now that runtime support is gone."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=FEATURE_SLUG).delete()


def restore_wikipedia_companion_suite_feature(apps, schema_editor):
    """Recreate the former mainstream Wikipedia Companion feature row on rollback."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.update_or_create(
        slug=FEATURE_SLUG,
        source="mainstream",
        defaults={
            "display": "Wikipedia Companion",
            "summary": (
                "Provides Wikipedia-powered data enrichment, including admin app detail widgets."
            ),
            "is_enabled": False,
            "main_app": None,
            "node_feature": None,
            "admin_requirements": (
                "App admin views may render Wikipedia context widgets only when this "
                "suite feature is enabled."
            ),
            "public_requirements": "No public-facing requirements.",
            "service_requirements": "Allow outbound Wikipedia API requests for summary enrichment.",
            "admin_views": ["admin:app_application_change"],
            "public_views": [],
            "service_views": ["apps.wikis.services.fetch_wiki_summary"],
            "code_locations": [
                "apps/wikis/widgets.py",
                "apps/wikis/services.py",
                "apps/wikis/templates/widgets/wiki_summary.html",
            ],
            "protocol_coverage": {},
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0046_seed_playwright_automation_suite_feature"),
    ]

    operations = [
        migrations.RunPython(
            remove_wikipedia_companion_suite_feature,
            reverse_code=restore_wikipedia_companion_suite_feature,
        ),
    ]
