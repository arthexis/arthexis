"""Seed the Operator Site Interface suite feature."""

from django.db import migrations


FEATURE_SLUG = "operator-site-interface"


def seed_feature(apps, schema_editor):
    """Create or update the Operator Site Interface suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.update_or_create(
        slug=FEATURE_SLUG,
        defaults={
            "display": "Operator Site Interface",
            "source": "mainstream",
            "summary": (
                "Controls whether the public site is shown and optionally redirects "
                "the root URL to a focused operator interface view."
            ),
            "is_enabled": True,
            "admin_requirements": "Admin interface must remain accessible when disabled.",
            "public_requirements": (
                "When enabled, show normal public site. When disabled, hide normal public "
                "site and optionally redirect to a configured interface landing without "
                "navigation chrome."
            ),
            "service_requirements": "No extra backend service requirements.",
            "admin_views": ["admin:pages_siteproxy_changelist"],
            "public_views": ["pages:index"],
            "service_views": [],
            "code_locations": [
                "apps/sites/views/landing.py",
                "apps/sites/templates/pages/base.html",
                "apps/sites/site_config.py",
            ],
            "protocol_coverage": {},
        },
    )


def unseed_feature(apps, schema_editor):
    """Remove the Operator Site Interface suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0017_merge_20260224_2131"),
    ]

    operations = [
        migrations.RunPython(seed_feature, unseed_feature),
    ]
