"""Seed the Pages Chat suite feature from fixture data."""

from __future__ import annotations

import json
from pathlib import Path

from django.db import migrations


FEATURE_SLUG = "pages-chat"
FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "features__pages_chat.json"


def _load_fixture_fields(path: Path, expected_slug: str) -> dict:
    """Return fixture fields for ``expected_slug`` or an empty mapping.

    Parameters:
        path: Fixture path to inspect.
        expected_slug: Feature slug expected inside the fixture payload.

    Returns:
        dict: Matching fixture field mapping, or an empty mapping when absent.
    """

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
    """Resolve optional fixture ``main_app`` value to an Application instance.

    Parameters:
        application_manager: Historical Application manager.
        main_app_value: Fixture ``main_app`` payload.

    Returns:
        Application | None: Matching application, when available.
    """

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


def seed_feature(apps, schema_editor):
    """Create or update the Pages Chat suite feature from fixture content.

    Parameters:
        apps: Django migration app registry.
        schema_editor: Active migration schema editor.

    Returns:
        None.
    """

    del schema_editor

    Feature = apps.get_model("features", "Feature")
    Application = apps.get_model("app", "Application")

    fields = _load_fixture_fields(FIXTURE_PATH, FEATURE_SLUG)
    if not fields:
        return

    application_manager = getattr(Application, "all_objects", Application._base_manager)
    feature_manager = getattr(Feature, "all_objects", Feature._base_manager)

    feature_manager.update_or_create(
        slug=FEATURE_SLUG,
        defaults={
            "display": fields.get("display", "Pages Chat"),
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
            "source": fields.get("source", "mainstream"),
            "is_seed_data": bool(fields.get("is_seed_data", True)),
            "is_deleted": bool(fields.get("is_deleted", False)),
        },
    )


def unseed_feature(apps, schema_editor):
    """Remove the seeded Pages Chat suite feature.

    Parameters:
        apps: Django migration app registry.
        schema_editor: Active migration schema editor.

    Returns:
        None.
    """

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    feature_manager = getattr(Feature, "all_objects", Feature._base_manager)
    feature_manager.filter(slug=FEATURE_SLUG).delete()


class Migration(migrations.Migration):
    """Seed the Pages Chat suite feature."""

    dependencies = [
        ("features", "0048_remove_development_blog_feature"),
    ]

    operations = [
        migrations.RunPython(seed_feature, unseed_feature),
    ]
