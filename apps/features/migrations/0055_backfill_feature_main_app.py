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

    from django.utils import timezone

    db_alias = schema_editor.connection.alias
    Application = apps.get_model("app", "Application")
    Feature = apps.get_model("features", "Feature")

    app_names: set[str] = set()
    features_by_app_name: dict[str, list[int]] = {}
    features = Feature.objects.using(db_alias).filter(main_app__isnull=True)
    for feature in features.iterator():
        app_name = _infer_main_app_name(feature.code_locations)
        if not app_name:
            continue
        app_names.add(app_name)
        features_by_app_name.setdefault(app_name, []).append(feature.pk)

    if not app_names:
        return

    existing_app_names = set(
        Application.objects.using(db_alias).filter(name__in=app_names).values_list("name", flat=True)
    )
    missing_apps = [Application(name=name) for name in app_names if name not in existing_app_names]
    if missing_apps:
        Application.objects.using(db_alias).bulk_create(missing_apps)

    app_id_by_name = dict(
        Application.objects.using(db_alias).filter(name__in=app_names).values_list("name", "pk")
    )
    now = timezone.localtime()
    for app_name, feature_pks in features_by_app_name.items():
        app_id = app_id_by_name.get(app_name)
        if not app_id:
            continue
        Feature.objects.using(db_alias).filter(pk__in=feature_pks).update(
            main_app_id=app_id,
            updated_at=now,
        )


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
