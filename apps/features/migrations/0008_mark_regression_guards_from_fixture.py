"""Seed additional regression guard tests for the standard charge point feature."""

from __future__ import annotations

import json
from pathlib import Path

from django.db import migrations


FEATURE_SLUG = "standard-charge-point"
FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "features__standard_charge_point.json"
)
REGRESSION_NODE_IDS: tuple[str, ...] = (
    "apps/nginx/tests/test_admin.py::test_generate_certificates_view_creates_certbot_certificate_when_selected",
    "apps/nginx/tests/test_admin.py::test_generate_certificates_view_creates_godaddy_certificate_when_selected",
    "apps/recipes/tests/test_recipes.py::test_execute_supports_bash_body_type",
    "apps/recipes/tests/test_recipes.py::test_execute_supports_bash_safe_normalized_kwarg_names",
    "apps/nodes/tests/test_registration_helpers.py::test_get_host_port_infers_from_proxy_headers[headers2-443]",
    "apps/ocpp/tests/test_coverage_ocpp16_command.py::test_ocpp16_coverage_matches_fixture",
    "apps/ocpp/tests/test_coverage_ocpp201_command.py::test_notify_display_messages_in_cp_to_csms_coverage",
    "apps/ocpp/tests/test_coverage_ocpp201_command.py::test_ocpp21_coverage_matches_fixture",
    "apps/ocpp/tests/test_coverage_ocpp201_command.py::test_ocpp201_coverage_matches_fixture",
)


def _load_regression_tests() -> list[tuple[str, str, str]]:
    if not FIXTURE_PATH.exists():
        return []
    try:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    tests: list[tuple[str, str, str]] = []
    for entry in payload:
        if not isinstance(entry, dict) or entry.get("model") != "features.featuretest":
            continue
        fields = entry.get("fields")
        if not isinstance(fields, dict):
            continue
        node_id = fields.get("node_id")
        if node_id not in REGRESSION_NODE_IDS:
            continue
        tests.append(
            (
                node_id,
                fields.get("name") or node_id,
                fields.get("notes") or "",
            )
        )
    return tests


def add_regression_guards(apps, schema_editor):
    """Upsert additional regression-guard tests for the standard feature seed."""

    del schema_editor

    Feature = apps.get_model("features", "Feature")
    FeatureTest = apps.get_model("features", "FeatureTest")

    feature_manager = getattr(Feature, "all_objects", Feature._base_manager)
    feature_test_manager = getattr(FeatureTest, "all_objects", FeatureTest._base_manager)

    feature = feature_manager.filter(slug=FEATURE_SLUG).first()
    if feature is None:
        return

    for node_id, name, notes in _load_regression_tests():
        feature_test_manager.update_or_create(
            feature=feature,
            node_id=node_id,
            defaults={
                "name": name,
                "notes": notes,
                "is_regression_guard": True,
                "is_seed_data": True,
                "is_deleted": False,
            },
        )


def remove_regression_guards(apps, schema_editor):
    """Delete the regression guards introduced by this migration."""

    del schema_editor

    Feature = apps.get_model("features", "Feature")
    FeatureTest = apps.get_model("features", "FeatureTest")

    feature_manager = getattr(Feature, "all_objects", Feature._base_manager)
    feature_test_manager = getattr(FeatureTest, "all_objects", FeatureTest._base_manager)

    feature = feature_manager.filter(slug=FEATURE_SLUG).first()
    if feature is None:
        return

    feature_test_manager.filter(feature=feature, node_id__in=REGRESSION_NODE_IDS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0007_seed_ocpp_ftp_reports"),
    ]

    operations = [
        migrations.RunPython(add_regression_guards, remove_regression_guards),
    ]
