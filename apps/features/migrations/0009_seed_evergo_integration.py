"""Seed the Evergo Integration suite feature from fixture data."""

from __future__ import annotations

import json
from pathlib import Path

from django.db import migrations


FEATURE_SLUG = "evergo-integration"
FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "features__evergo_integration.json"
)


def _load_fixture_entries() -> list[dict]:
    """Load feature fixture entries for this migration."""

    if not FIXTURE_PATH.exists():
        return []
    try:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return payload if isinstance(payload, list) else []


def seed_evergo_integration(apps, schema_editor):
    """Insert or update the Evergo Integration feature definition."""

    del schema_editor

    Feature = apps.get_model("features", "Feature")
    Application = apps.get_model("app", "Application")

    feature_manager = getattr(Feature, "all_objects", Feature._base_manager)
    application_manager = getattr(Application, "all_objects", Application._base_manager)

    for entry in _load_fixture_entries():
        if not isinstance(entry, dict) or entry.get("model") != "features.feature":
            continue
        fields = entry.get("fields")
        if not isinstance(fields, dict):
            continue
        slug = fields.get("slug")
        if slug != FEATURE_SLUG:
            continue

        app_obj = None
        main_app = fields.get("main_app")
        if isinstance(main_app, (list, tuple)) and main_app:
            app_name = str(main_app[0]).strip()
            if app_name:
                app_obj, _ = application_manager.get_or_create(
                    name=app_name,
                    defaults={"description": ""},
                )

        feature_manager.update_or_create(
            slug=slug,
            defaults={
                "display": fields.get("display", ""),
                "summary": fields.get("summary", ""),
                "is_enabled": bool(fields.get("is_enabled", True)),
                "main_app": app_obj,
                "node_feature": None,
                "admin_requirements": fields.get("admin_requirements", ""),
                "public_requirements": fields.get("public_requirements", ""),
                "service_requirements": fields.get("service_requirements", ""),
                "admin_views": fields.get("admin_views", []) or [],
                "public_views": fields.get("public_views", []) or [],
                "service_views": fields.get("service_views", []) or [],
                "code_locations": fields.get("code_locations", []) or [],
                "protocol_coverage": fields.get("protocol_coverage", {}) or {},
                "is_seed_data": bool(fields.get("is_seed_data", True)),
                "is_deleted": bool(fields.get("is_deleted", False)),
            },
        )


def unseed_evergo_integration(apps, schema_editor):
    """Remove the seeded Evergo Integration feature."""

    del schema_editor

    Feature = apps.get_model("features", "Feature")
    feature_manager = getattr(Feature, "all_objects", Feature._base_manager)
    feature_manager.filter(slug=FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0008_mark_regression_guards_from_fixture"),
    ]

    operations = [
        migrations.RunPython(seed_evergo_integration, unseed_evergo_integration),
    ]
