"""Rename OCPP 1.6 user-facing labels to OCPP 1.6J."""

from django.db import migrations


OLD_SERVICE_REQUIREMENTS = (
    "Create charger records for supported OCPP 1.6 sessions on first connect, "
    "register charge points to the local node, and enforce admission rules for known "
    "chargers when creation is disabled."
)
NEW_SERVICE_REQUIREMENTS = (
    "Create charger records for supported OCPP 1.6J sessions on first connect, "
    "register charge points to the local node, and enforce admission rules for known "
    "chargers when creation is disabled."
)

OLD_SUMMARY = "Support OCPP 1.6 charge point onboarding and call handling."
NEW_SUMMARY = "Support OCPP 1.6J charge point onboarding and call handling."

OLD_DISPLAY = "OCPP 1.6 Charge Point"
NEW_DISPLAY = "OCPP 1.6J Charge Point"

OLD_TEST_NAME = "OCPP 1.6 coverage report matches fixture"
NEW_TEST_NAME = "OCPP 1.6J coverage report matches fixture"


def forward_update_ocpp_16j_labels(apps, schema_editor):
    """Update OCPP 1.6-facing feature labels to OCPP 1.6J."""

    feature_model = apps.get_model("features", "Feature")
    feature_test_model = apps.get_model("features", "FeatureTest")

    feature_model.objects.filter(slug="ocpp-16-charge-point").update(
        display=NEW_DISPLAY,
        summary=NEW_SUMMARY,
        service_requirements=NEW_SERVICE_REQUIREMENTS,
    )
    feature_test_model.objects.filter(
        feature__slug="ocpp-16-charge-point",
        name=OLD_TEST_NAME,
    ).update(name=NEW_TEST_NAME)


def reverse_update_ocpp_16j_labels(apps, schema_editor):
    """Restore OCPP 1.6-facing feature labels when reversing migration."""

    feature_model = apps.get_model("features", "Feature")
    feature_test_model = apps.get_model("features", "FeatureTest")

    feature_model.objects.filter(slug="ocpp-16-charge-point").update(
        display=OLD_DISPLAY,
        summary=OLD_SUMMARY,
        service_requirements=OLD_SERVICE_REQUIREMENTS,
    )
    feature_test_model.objects.filter(
        feature__slug="ocpp-16-charge-point",
        name=NEW_TEST_NAME,
    ).update(name=OLD_TEST_NAME)


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0028_merge_20260301_1900"),
    ]

    operations = [
        migrations.RunPython(
            forward_update_ocpp_16j_labels,
            reverse_code=reverse_update_ocpp_16j_labels,
        ),
    ]
