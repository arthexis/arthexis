"""Rework the seeded Evergo feature definition to Evergo API Client."""

from __future__ import annotations

import json
from pathlib import Path

from django.db import migrations


OLD_FEATURE_SLUG = "evergo-integration"
NEW_FEATURE_SLUG = "evergo-api-client"
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
NEW_FIXTURE_PATH = FIXTURES_DIR / "features__evergo_api_client.json"
OLD_FIXTURE_PATH = FIXTURES_DIR / "features__evergo_integration.json"


def _load_fixture_entry(path: Path, expected_slug: str) -> dict:
    """Load and return a single matching feature fixture entry."""

    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(payload, list):
        return {}

    for entry in payload:
        if not isinstance(entry, dict) or entry.get("model") != "features.feature":
            continue
        fields = entry.get("fields")
        if not isinstance(fields, dict):
            continue
        if fields.get("slug") == expected_slug:
            return fields

    return {}


def _resolve_main_app(application_manager, main_app_value):
    """Resolve the configured main app object from fixture data."""

    if not isinstance(main_app_value, (list, tuple)) or not main_app_value:
        return None

    app_name = str(main_app_value[0]).strip()
    if not app_name:
        return None

    app_obj, _ = application_manager.get_or_create(
        name=app_name,
        defaults={"description": ""},
    )
    return app_obj


def _build_feature_defaults(application_manager, fields: dict) -> dict:
    """Build feature defaults payload from fixture fields."""

    return {
        "display": fields.get("display", ""),
        "summary": fields.get("summary", ""),
        "is_enabled": bool(fields.get("is_enabled", True)),
        "main_app": _resolve_main_app(application_manager, fields.get("main_app")),
        "node_feature": None,
        "admin_requirements": fields.get("admin_requirements", ""),
        "public_requirements": fields.get("public_requirements", ""),
        "service_requirements": fields.get("service_requirements", ""),
        "admin_views": fields.get("admin_views") or [],
        "public_views": fields.get("public_views") or [],
        "service_views": fields.get("service_views") or [],
        "code_locations": fields.get("code_locations") or [],
        "protocol_coverage": fields.get("protocol_coverage") or {},
        "is_seed_data": bool(fields.get("is_seed_data", True)),
        "is_deleted": bool(fields.get("is_deleted", False)),
    }


def _reassign_related_rows(source_feature, target_feature):
    """Move FK-backed rows pointing at source feature onto target feature."""

    for relation in source_feature._meta.related_objects:
        if relation.many_to_many:
            continue

        field_name = relation.field.name
        related_model = relation.related_model
        related_model._base_manager.filter(**{field_name: source_feature.pk}).update(
            **{field_name: target_feature.pk}
        )


def _reseed_feature(apps, source_slug: str, target_slug: str, fixture_path: Path):
    """Reseed a feature entry from fixture data, supporting slug migration."""

    Feature = apps.get_model("features", "Feature")
    Application = apps.get_model("app", "Application")

    feature_manager = getattr(Feature, "all_objects", Feature._base_manager)
    application_manager = getattr(Application, "all_objects", Application._base_manager)

    fields = _load_fixture_entry(fixture_path, expected_slug=target_slug)
    if not fields:
        return

    defaults = _build_feature_defaults(application_manager, fields)

    source_feature = feature_manager.filter(slug=source_slug).first()
    target_feature = feature_manager.filter(slug=target_slug).first()

    if source_feature and not target_feature:
        source_feature.slug = target_slug
        for key, value in defaults.items():
            setattr(source_feature, key, value)
        source_feature.save(
            update_fields=["slug", *defaults.keys()],
        )
        return

    target_feature, _ = feature_manager.update_or_create(
        slug=target_slug,
        defaults=defaults,
    )

    if source_slug != target_slug:
        if source_feature and target_feature and source_feature.pk != target_feature.pk:
            _reassign_related_rows(source_feature, target_feature)
        feature_manager.filter(slug=source_slug).delete()


def rework_to_evergo_api_client(apps, schema_editor):
    """Rename and update Evergo Integration feature to Evergo API Client."""

    del schema_editor

    _reseed_feature(
        apps,
        source_slug=OLD_FEATURE_SLUG,
        target_slug=NEW_FEATURE_SLUG,
        fixture_path=NEW_FIXTURE_PATH,
    )


def rollback_to_evergo_integration(apps, schema_editor):
    """Restore Evergo API Client feature back to Evergo Integration."""

    del schema_editor

    _reseed_feature(
        apps,
        source_slug=NEW_FEATURE_SLUG,
        target_slug=OLD_FEATURE_SLUG,
        fixture_path=OLD_FIXTURE_PATH,
    )


class Migration(migrations.Migration):
    """Apply the Evergo feature rework fixture migration."""

    dependencies = [
        ("features", "0010_seed_ocpp_version_charge_point_features"),
    ]

    operations = [
        migrations.RunPython(rework_to_evergo_api_client, rollback_to_evergo_integration),
    ]
