"""Regression checks that enforce admin UI framework usage in templates."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ADMIN_TEMPLATES_ROOT = PROJECT_ROOT / "apps"
CUSTOM_ADMIN_CSS_OVERRIDE_MARKER = "admin-ui-framework: allow-custom-css"
CUSTOM_CSS_SNIPPETS = ("<style", 'rel="stylesheet"', "rel='stylesheet'")

# Baseline set of templates that still ship custom admin CSS.
#
# This legacy allowlist prevents churn while we roll existing pages over to
# shared framework primitives. New templates should not be added to this set;
# instead, add the override marker with rationale in-template when truly needed.
LEGACY_CUSTOM_ADMIN_CSS_TEMPLATES = {
    "apps/base/templates/admin/base/model_export.html",
    "apps/core/templates/admin/change_list.html",
    "apps/core/templates/admin/core/clientreport/generate.html",
    "apps/core/templates/admin/core/product/search_orders_for_selected.html",
    "apps/core/templates/admin/edit_inline/profile_stacked.html",
    "apps/core/templates/admin/includes/seed_datum_styles.html",
    "apps/core/templates/admin/index.html",
    "apps/core/templates/admin/system_changelog_report.html",
    "apps/core/templates/admin/system_startup_report.html",
    "apps/core/templates/admin/system_upgrade_report.html",
    "apps/core/templates/admin/system_uptime_report.html",
    "apps/locals/templates/admin/data_list.html",
    "apps/locals/templates/admin/favorite_list.html",
    "apps/maps/templates/admin/maps/location/add_current.html",
    "apps/nginx/templates/admin/nginx/siteconfiguration/change_list.html",
    "apps/nginx/templates/admin/nginx/siteconfiguration/preview.html",
    "apps/nodes/templates/admin/nodes/netmessage/send.html",
    "apps/nodes/templates/admin/nodes/node/register_visitor.html",
    "apps/nodes/templates/admin/nodes/node/run_task.html",
    "apps/nodes/templates/admin/nodes/nodefeature/view_stream.html",
    "apps/ocpp/templates/admin/ocpp/charger/change_form.html",
    "apps/ocpp/templates/admin/ocpp/charger/change_list.html",
    "apps/ocpp/templates/admin/ocpp/charger/setup_location.html",
    "apps/ocpp/templates/admin/ocpp/chargerconfiguration/configuration_inline.html",
    "apps/ocpp/templates/admin/ocpp/chargerconfiguration/push_configuration.html",
    "apps/ocpp/templates/admin/ocpp/log_view.html",
    "apps/ocpp/templates/admin/ocpp/simulator/change_list.html",
    "apps/reports/templates/admin/system_sql_report.html",
    "apps/sensors/templates/admin/sensors/thermometer/thermometer_trends.html",
    "apps/sites/templates/admin/app_index.html",
    "apps/sites/templates/admin/base_site.html",
    "apps/sites/templates/admin/change_form.html",
    "apps/sites/templates/admin/change_list.html",
    "apps/sites/templates/admin/includes/dashboard_styles.html",
    "apps/sites/templates/admin/includes/related_models_styles.html",
    "apps/sites/templates/admin/index.html",
    "apps/sites/templates/admin/log_viewer.html",
    "apps/sites/templates/admin/login.html",
    "apps/sites/templates/admin/model_graph.html",
    "apps/sites/templates/admin/pages/viewhistory/traffic_graph.html",
    "apps/sites/templates/admin/teams/slack_bot_wizard.html",
    "apps/totp/templates/admin/totp/device_wizard.html",
    "apps/video/templates/admin/video/videodevice/change_form.html",
    "apps/video/templates/admin/video/view_stream.html",
}


def _admin_template_paths() -> list[Path]:
    """Return every admin HTML template path under the apps/ tree."""

    template_paths: list[Path] = []
    for app_path in sorted(ADMIN_TEMPLATES_ROOT.iterdir()):
        admin_templates_path = app_path / "templates" / "admin"
        if admin_templates_path.is_dir():
            template_paths.extend(sorted(admin_templates_path.rglob("*.html")))
    return template_paths


def _contains_custom_css(template_text: str) -> bool:
    """Return whether template text includes inline/custom stylesheet declarations."""

    lowered = template_text.lower()
    return any(snippet in lowered for snippet in CUSTOM_CSS_SNIPPETS)


def test_admin_templates_forbid_new_custom_css_without_override_marker() -> None:
    """Regression: block new admin custom CSS unless a template adds an explicit override marker."""

    non_compliant_templates: list[str] = []
    for template_path in _admin_template_paths():
        relative_path = template_path.relative_to(PROJECT_ROOT).as_posix()
        if relative_path in LEGACY_CUSTOM_ADMIN_CSS_TEMPLATES:
            continue
        template_text = template_path.read_text(encoding="utf-8")
        if not _contains_custom_css(template_text):
            continue
        if CUSTOM_ADMIN_CSS_OVERRIDE_MARKER in template_text:
            continue
        non_compliant_templates.append(relative_path)

    assert not non_compliant_templates, (
        "New admin templates must rely on shared admin_ui_framework.css primitives. "
        "If custom CSS is required for a specific template, add an in-template "
        f"comment marker '{CUSTOM_ADMIN_CSS_OVERRIDE_MARKER}' with rationale: "
        f"{non_compliant_templates}"
    )
