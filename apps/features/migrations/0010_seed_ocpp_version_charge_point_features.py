"""Seed per-version OCPP charge point suite features from fixture data."""

from __future__ import annotations

import json
from pathlib import Path

from django.db import migrations


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "features__standard_charge_point.json"
)
SUPPORTED_FEATURE_SLUGS = {
    "ocpp-16-charge-point",
    "ocpp-201-charge-point",
    "ocpp-21-charge-point",
}
LEGACY_FEATURE_SLUG = "standard-charge-point"


def _load_fixture_entries() -> list[dict]:
    """Return fixture entries for OCPP charge point suite features."""

    if not FIXTURE_PATH.exists():
        return []
    try:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _resolve_application(application_manager, main_app_field):
    """Resolve or create the Application referenced by fixture data."""

    if not isinstance(main_app_field, (list, tuple)) or not main_app_field:
        return None
    app_name = str(main_app_field[0]).strip()
    if not app_name:
        return None
    app_obj, _ = application_manager.get_or_create(
        name=app_name,
        defaults={"description": ""},
    )
    return app_obj


def _resolve_node_feature(node_feature_manager, node_feature_field):
    """Resolve node feature referenced by fixture data."""

    if not isinstance(node_feature_field, (list, tuple)) or not node_feature_field:
        return None
    node_slug = str(node_feature_field[0]).strip()
    if not node_slug:
        return None
    return node_feature_manager.filter(slug=node_slug).first()


def _seed_features_and_tests(apps, *, entries: list[dict]) -> None:
    """Create or update supported OCPP version features and tests."""

    Feature = apps.get_model("features", "Feature")
    FeatureTest = apps.get_model("features", "FeatureTest")
    Application = apps.get_model("app", "Application")
    NodeFeature = apps.get_model("nodes", "NodeFeature")

    feature_manager = getattr(Feature, "all_objects", Feature._base_manager)
    feature_test_manager = getattr(FeatureTest, "all_objects", FeatureTest._base_manager)
    application_manager = getattr(Application, "all_objects", Application._base_manager)
    node_feature_manager = getattr(NodeFeature, "all_objects", NodeFeature._base_manager)

    features_by_slug = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("model") != "features.feature":
            continue
        fields = entry.get("fields")
        if not isinstance(fields, dict):
            continue
        slug = fields.get("slug")
        if slug not in SUPPORTED_FEATURE_SLUGS:
            continue

        app_obj = _resolve_application(application_manager, fields.get("main_app"))
        node_obj = _resolve_node_feature(node_feature_manager, fields.get("node_feature"))

        feature_obj, _ = feature_manager.update_or_create(
            slug=slug,
            defaults={
                "display": fields.get("display", ""),
                "summary": fields.get("summary", ""),
                "is_enabled": bool(fields.get("is_enabled", True)),
                "main_app": app_obj,
                "node_feature": node_obj,
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
        features_by_slug[slug] = feature_obj

    for entry in entries:
        if not isinstance(entry, dict) or entry.get("model") != "features.featuretest":
            continue
        fields = entry.get("fields")
        if not isinstance(fields, dict):
            continue
        feature_field = fields.get("feature")
        if not isinstance(feature_field, (list, tuple)) or not feature_field:
            continue
        feature_slug = str(feature_field[0]).strip()
        if feature_slug not in SUPPORTED_FEATURE_SLUGS:
            continue
        feature_obj = features_by_slug.get(feature_slug)
        if feature_obj is None:
            continue

        node_id = str(fields.get("node_id", "")).strip()
        if not node_id:
            continue

        feature_test_manager.update_or_create(
            feature=feature_obj,
            node_id=node_id,
            defaults={
                "name": fields.get("name", node_id),
                "is_regression_guard": bool(fields.get("is_regression_guard", True)),
                "notes": fields.get("notes", ""),
                "is_seed_data": bool(fields.get("is_seed_data", True)),
                "is_deleted": bool(fields.get("is_deleted", False)),
            },
        )


def forwards(apps, schema_editor):
    """Seed per-version OCPP charge point features and remove legacy feature."""

    del schema_editor

    entries = _load_fixture_entries()
    _seed_features_and_tests(apps, entries=entries)

    Feature = apps.get_model("features", "Feature")
    FeatureTest = apps.get_model("features", "FeatureTest")
    feature_manager = getattr(Feature, "all_objects", Feature._base_manager)
    feature_test_manager = getattr(FeatureTest, "all_objects", FeatureTest._base_manager)

    legacy_feature = feature_manager.filter(slug=LEGACY_FEATURE_SLUG).first()
    if legacy_feature is not None:
        feature_test_manager.filter(feature=legacy_feature).delete()
        legacy_feature.delete()


def backwards(apps, schema_editor):
    """Remove versioned OCPP features and restore the legacy charge-point feature."""

    del schema_editor

    Feature = apps.get_model("features", "Feature")
    FeatureTest = apps.get_model("features", "FeatureTest")
    Application = apps.get_model("app", "Application")
    NodeFeature = apps.get_model("nodes", "NodeFeature")

    feature_manager = getattr(Feature, "all_objects", Feature._base_manager)
    feature_test_manager = getattr(FeatureTest, "all_objects", FeatureTest._base_manager)
    application_manager = getattr(Application, "all_objects", Application._base_manager)
    node_feature_manager = getattr(NodeFeature, "all_objects", NodeFeature._base_manager)

    versioned_features = feature_manager.filter(slug__in=sorted(SUPPORTED_FEATURE_SLUGS))
    feature_test_manager.filter(feature__in=versioned_features).delete()
    versioned_features.delete()

    app_obj, _ = application_manager.get_or_create(name="ocpp", defaults={"description": ""})
    node_feature = node_feature_manager.filter(slug="charge-points").first()
    feature_manager.update_or_create(
        slug=LEGACY_FEATURE_SLUG,
        defaults={
            "display": "Standard Charge Point",
            "summary": "Allow the CSMS to auto-create and register new charge points on first websocket connect. When disabled, only known chargers can connect.",
            "is_enabled": True,
            "main_app": app_obj,
            "node_feature": node_feature,
            "admin_requirements": "Provide charger administration, monitoring dashboards, and simulator controls.",
            "public_requirements": "Expose charger status pages, connector details, and session lookups for operators.",
            "service_requirements": "Create charger records on first connect, register charge points to the local node, and allow OCPP traffic for known chargers when creation is disabled.",
            "admin_views": ["admin:ocpp_charger_changelist", "admin:ocpp_simulator_changelist"],
            "public_views": ["ocpp:ocpp-dashboard", "ocpp:charger-page", "ocpp:charger-status"],
            "service_views": [
                "WebSocket: /<charge_point_id>/",
                "WebSocket: /ws/<charge_point_id>/",
                "Service: apps.ocpp.consumers.CSMSConsumer",
            ],
            "code_locations": [
                "apps/ocpp/consumers.py",
                "apps/ocpp/views/actions",
                "apps/ocpp/views/common.py",
                "apps/ocpp/store.py",
                "apps/ocpp/coverage.json",
                "apps/ocpp/coverage201.json",
                "apps/ocpp/coverage21.json",
            ],
            "protocol_coverage": {},
            "is_seed_data": True,
            "is_deleted": False,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0009_seed_evergo_integration"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
