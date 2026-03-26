"""Backfill missing suite-feature main app links from code locations."""

from __future__ import annotations

from django.db import migrations


def _infer_main_app_name(code_locations):
    """Infer an app label from fixture-style ``code_locations`` entries."""

    if not isinstance(code_locations, list):
        return None

    for location in code_locations:
        if not isinstance(location, str):
            continue
        location_parts = [part for part in location.strip(" /").split("/") if part]
        if len(location_parts) < 2 or location_parts[0] != "apps":
            continue
        label = location_parts[1].strip()
        if label:
            return label
    return None


def backfill_missing_feature_main_app(apps, schema_editor):
    """Populate ``Feature.main_app`` for rows that currently have no classification."""

    del schema_editor
    Application = apps.get_model("app", "Application")
    Feature = apps.get_model("features", "Feature")

    features = Feature.objects.filter(main_app__isnull=True)
    for feature in features.iterator():
        app_name = _infer_main_app_name(feature.code_locations)
        if not app_name:
            continue
        app, _ = Application.objects.get_or_create(name=app_name)
        feature.main_app = app
        feature.save(update_fields=["main_app", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0054_seed_raspberry_pi_imager_suite_feature"),
    ]

    operations = [
        migrations.RunPython(
            backfill_missing_feature_main_app,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
