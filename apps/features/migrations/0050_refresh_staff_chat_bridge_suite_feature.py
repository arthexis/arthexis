"""Refresh Staff Chat Bridge suite feature metadata from fixture data."""

from __future__ import annotations

import json
from pathlib import Path

from django.db import migrations


FEATURE_SLUG = "staff-chat-bridge"
FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "features__staff_chat_bridge.json"
REVERSE_FIXTURE_FIELDS = {
    "display": "Staff Chat Bridge",
    "summary": "Gates staff-facing chat bridge UI wiring for site and admin chat widgets.",
    "is_enabled": True,
    "main_app": ["sites"],
    "node_feature": None,
    "admin_requirements": (
        "Admin base template should only render the chat widget when this suite "
        "feature is enabled."
    ),
    "public_requirements": (
        "Public base template should only render the chat widget when this suite "
        "feature is enabled."
    ),
    "service_requirements": (
        "No additional backend services beyond configured pages chat socket path."
    ),
    "admin_views": ["admin:index"],
    "public_views": ["pages:index"],
    "service_views": ["settings:PAGES_CHAT_SOCKET_PATH"],
    "code_locations": [
        "apps/sites/context_processors.py",
        "apps/sites/templates/pages/base.html",
        "apps/sites/templates/admin/base_site.html",
    ],
    "protocol_coverage": {},
    "source": "mainstream",
    "is_seed_data": True,
    "is_deleted": False,
}


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


def refresh_feature(apps, schema_editor):
    """Create or update the Staff Chat Bridge suite feature from fixture content.

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

    _update_feature_from_fields(
        feature_manager=feature_manager,
        application_manager=application_manager,
        fields=fields,
    )


def restore_feature(apps, schema_editor):
    """Restore Staff Chat Bridge metadata that existed before this refresh.

    Parameters:
        apps: Django migration app registry.
        schema_editor: Active migration schema editor.

    Returns:
        None.
    """

    del schema_editor

    Feature = apps.get_model("features", "Feature")
    Application = apps.get_model("app", "Application")

    application_manager = getattr(Application, "all_objects", Application._base_manager)
    feature_manager = getattr(Feature, "all_objects", Feature._base_manager)

    _update_feature_from_fields(
        feature_manager=feature_manager,
        application_manager=application_manager,
        fields=REVERSE_FIXTURE_FIELDS,
    )


def _update_feature_from_fields(*, feature_manager, application_manager, fields: dict) -> None:
    """Apply a fixture-like field mapping to the Staff Chat Bridge feature.

    Parameters:
        feature_manager: Historical manager used to write feature rows.
        application_manager: Historical manager used to resolve ``main_app``.
        fields: Fixture-style field mapping to persist.

    Returns:
        None.
    """

    feature_manager.update_or_create(
        slug=FEATURE_SLUG,
        defaults={
            "display": fields.get("display", "Staff Chat Bridge"),
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


class Migration(migrations.Migration):
    """Refresh Staff Chat Bridge suite feature metadata."""

    dependencies = [
        ("features", "0049_seed_pages_chat_suite_feature"),
    ]

    operations = [
        migrations.RunPython(refresh_feature, restore_feature),
    ]
