"""Seed the Wikipedia Companion suite feature."""

from django.db import migrations


WIKIPEDIA_COMPANION_FEATURE_SLUG = "wikipedia-companion"


def seed_wikipedia_companion_suite_feature(apps, schema_editor):
    """Create or update the Wikipedia Companion suite feature definition."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    defaults = {
        "display": "Wikipedia Companion",
        "source": "mainstream",
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
    }

    updated_count = Feature.objects.filter(
        slug=WIKIPEDIA_COMPANION_FEATURE_SLUG,
        source="mainstream",
    ).update(**defaults)
    if updated_count:
        return

    if Feature.objects.filter(slug=WIKIPEDIA_COMPANION_FEATURE_SLUG).exists():
        return

    Feature.objects.create(
        slug=WIKIPEDIA_COMPANION_FEATURE_SLUG,
        **defaults,
    )


def unseed_wikipedia_companion_suite_feature(apps, schema_editor):
    """Delete the seeded Wikipedia Companion suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(
        slug=WIKIPEDIA_COMPANION_FEATURE_SLUG,
        source="mainstream",
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0036_seed_ocpp_forwarder_suite_feature"),
    ]

    operations = [
        migrations.RunPython(
            seed_wikipedia_companion_suite_feature,
            unseed_wikipedia_companion_suite_feature,
        ),
    ]
