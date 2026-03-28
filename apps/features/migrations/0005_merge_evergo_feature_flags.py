from __future__ import annotations

from django.db import migrations


CANONICAL_SLUG = "evergo-api-client"
LEGACY_SLUG = "evergo-integration"
CANONICAL_DISPLAY = "Evergo Integration"
CANONICAL_SUMMARY = (
    "Bind Suite users to Evergo credentials and synchronize profile, customer, "
    "and order metadata through Evergo endpoints."
)
CANONICAL_ADMIN_REQUIREMENTS = (
    "Provide admin model management for Evergo credentials with actions to "
    "validate authentication and load customer/order data."
)
CANONICAL_SERVICE_REQUIREMENTS = (
    "Provide a Django management command for CLI credential setup, login "
    "validation, and data synchronization."
)


def _merge_evergo_features(apps, schema_editor):
    Feature = apps.get_model("features", "Feature")
    FeatureNote = apps.get_model("features", "FeatureNote")
    FeatureTest = apps.get_model("features", "FeatureTest")

    canonical = Feature.objects.filter(slug=CANONICAL_SLUG).first()
    legacy = Feature.objects.filter(slug=LEGACY_SLUG).first()

    if canonical is None and legacy is None:
        return

    if canonical is None and legacy is not None:
        legacy.slug = CANONICAL_SLUG
        legacy.display = CANONICAL_DISPLAY
        legacy.summary = CANONICAL_SUMMARY
        legacy.admin_requirements = CANONICAL_ADMIN_REQUIREMENTS
        legacy.service_requirements = CANONICAL_SERVICE_REQUIREMENTS
        legacy.source = "mainstream"
        legacy.save(
            update_fields=[
                "slug",
                "display",
                "summary",
                "admin_requirements",
                "service_requirements",
                "source",
                "updated_at",
            ]
        )
        return

    if legacy is None:
        canonical.display = CANONICAL_DISPLAY
        canonical.summary = CANONICAL_SUMMARY
        canonical.admin_requirements = CANONICAL_ADMIN_REQUIREMENTS
        canonical.service_requirements = CANONICAL_SERVICE_REQUIREMENTS
        canonical.source = "mainstream"
        canonical.save(
            update_fields=[
                "display",
                "summary",
                "admin_requirements",
                "service_requirements",
                "source",
                "updated_at",
            ]
        )
        return

    FeatureNote.objects.filter(feature_id=legacy.pk).update(feature_id=canonical.pk)
    FeatureTest.objects.filter(feature_id=legacy.pk).update(feature_id=canonical.pk)

    canonical.display = CANONICAL_DISPLAY
    canonical.summary = CANONICAL_SUMMARY
    canonical.admin_requirements = CANONICAL_ADMIN_REQUIREMENTS
    canonical.service_requirements = CANONICAL_SERVICE_REQUIREMENTS
    canonical.source = "mainstream"
    canonical.save(
        update_fields=[
            "display",
            "summary",
            "admin_requirements",
            "service_requirements",
            "source",
            "updated_at",
        ]
    )

    Feature.objects.filter(pk=legacy.pk).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0004_initial"),
    ]

    operations = [
        migrations.RunPython(_merge_evergo_features, migrations.RunPython.noop),
    ]
