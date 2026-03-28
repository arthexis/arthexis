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
CANONICAL_UPDATE_DATA = {
    "display": CANONICAL_DISPLAY,
    "summary": CANONICAL_SUMMARY,
    "admin_requirements": CANONICAL_ADMIN_REQUIREMENTS,
    "service_requirements": CANONICAL_SERVICE_REQUIREMENTS,
    "source": "mainstream",
}
CANONICAL_UPDATE_FIELDS = [*CANONICAL_UPDATE_DATA, "updated_at"]


def _apply_canonical_values(feature, *, include_slug: bool = False):
    for field, value in CANONICAL_UPDATE_DATA.items():
        setattr(feature, field, value)

    update_fields = ["slug", *CANONICAL_UPDATE_FIELDS] if include_slug else CANONICAL_UPDATE_FIELDS
    feature.save(update_fields=update_fields)


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
        _apply_canonical_values(legacy, include_slug=True)
        return

    if legacy is not None:
        FeatureNote.objects.filter(feature_id=legacy.pk).update(feature_id=canonical.pk)
        canonical_node_ids = FeatureTest.objects.filter(feature_id=canonical.pk).values_list(
            "node_id", flat=True
        )
        FeatureTest.objects.filter(feature_id=legacy.pk, node_id__in=canonical_node_ids).delete()
        FeatureTest.objects.filter(feature_id=legacy.pk).update(feature_id=canonical.pk)
        Feature.objects.filter(pk=legacy.pk).delete()

    _apply_canonical_values(canonical)


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0004_initial"),
    ]

    operations = [
        migrations.RunPython(_merge_evergo_features, migrations.RunPython.noop),
    ]
