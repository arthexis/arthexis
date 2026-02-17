"""Seed additional regression guard tests for the standard charge point feature."""

from __future__ import annotations

from django.db import migrations


FEATURE_SLUG = "standard-charge-point"
REGRESSION_TESTS: tuple[tuple[str, str, str], ...] = (
    (
        "apps/nginx/tests/test_admin.py::test_generate_certificates_view_creates_certbot_certificate_when_selected",
        "Certbot certificate generation is provisioned from admin",
        "Guards HTTPS admin certificate provisioning for certbot.",
    ),
    (
        "apps/nginx/tests/test_admin.py::test_generate_certificates_view_creates_godaddy_certificate_when_selected",
        "GoDaddy DNS certificate generation is provisioned from admin",
        "Guards HTTPS admin certificate provisioning for DNS challenge flow.",
    ),
    (
        "apps/recipes/tests/test_recipes.py::test_execute_supports_bash_body_type",
        "Bash recipe execution returns stdout",
        "Protects bash runtime support for recipes.",
    ),
    (
        "apps/recipes/tests/test_recipes.py::test_execute_supports_bash_safe_normalized_kwarg_names",
        "Bash recipe kwargs normalize to safe env names",
        "Protects normalized kwargs behavior in bash recipe execution.",
    ),
    (
        "apps/nodes/tests/test_registration_helpers.py::test_get_host_port_infers_from_proxy_headers[headers2-443]",
        "Host port inference honors HTTPS forwarded proto",
        "Guards proxy header inference for HTTPS visitor registration.",
    ),
    (
        "apps/ocpp/tests/test_coverage_ocpp16_command.py::test_ocpp16_coverage_matches_fixture",
        "OCPP 1.6 coverage report matches fixture",
        "Guards protocol coverage command output against committed fixture.",
    ),
    (
        "apps/ocpp/tests/test_coverage_ocpp201_command.py::test_notify_display_messages_in_cp_to_csms_coverage",
        "OCPP 2.x coverage includes NotifyDisplayMessages",
        "Guards cp-to-csms action coverage extraction for NotifyDisplayMessages.",
    ),
    (
        "apps/ocpp/tests/test_coverage_ocpp201_command.py::test_ocpp21_coverage_matches_fixture",
        "OCPP 2.1 coverage report matches fixture",
        "Guards OCPP 2.1 coverage command output against committed fixture.",
    ),
    (
        "apps/ocpp/tests/test_coverage_ocpp201_command.py::test_ocpp201_coverage_matches_fixture",
        "OCPP 2.0.1 coverage report matches fixture",
        "Guards OCPP 2.0.1 coverage command output against committed fixture.",
    ),
)


def add_regression_guards(apps, schema_editor):
    """Upsert additional regression-guard tests for the standard feature seed."""

    del schema_editor

    Feature = apps.get_model("features", "Feature")
    FeatureTest = apps.get_model("features", "FeatureTest")

    feature = Feature.objects.filter(slug=FEATURE_SLUG).first()
    if feature is None:
        return

    for node_id, name, notes in REGRESSION_TESTS:
        FeatureTest.objects.update_or_create(
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

    feature = Feature.objects.filter(slug=FEATURE_SLUG).first()
    if feature is None:
        return

    node_ids = [node_id for node_id, _name, _notes in REGRESSION_TESTS]
    FeatureTest.objects.filter(feature=feature, node_id__in=node_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0007_seed_ocpp_ftp_reports"),
    ]

    operations = [
        migrations.RunPython(add_regression_guards, remove_regression_guards),
    ]
