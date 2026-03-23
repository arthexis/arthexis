"""Move Odoo sync integration toggles into Odoo CRM Sync feature parameters."""

from __future__ import annotations

from django.db import migrations


SUITE_FEATURE_SLUG = "odoo-crm-sync"
DEPLOYMENT_DISCOVERY_FEATURE_SLUG = "odoo-sync-deployment-discovery"
EMPLOYEE_IMPORT_FEATURE_SLUG = "odoo-sync-employee-import"
EVERGO_USERS_FEATURE_SLUG = "odoo-sync-evergo-users"


def _normalize_metadata(metadata: object) -> dict:
    """Return dictionary metadata payload."""

    return metadata.copy() if isinstance(metadata, dict) else {}


def forward(apps, schema_editor):
    """Persist integration states as parameters while preserving legacy feature rows."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")

    suite_feature = Feature.objects.filter(slug=SUITE_FEATURE_SLUG).first()
    if suite_feature:
        metadata = _normalize_metadata(suite_feature.metadata)
        parameters = metadata.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {}

        integration_map = {
            "deployment_discovery": DEPLOYMENT_DISCOVERY_FEATURE_SLUG,
            "employee_import": EMPLOYEE_IMPORT_FEATURE_SLUG,
            "evergo_users": EVERGO_USERS_FEATURE_SLUG,
        }
        for parameter_key, integration_slug in integration_map.items():
            integration_enabled = (
                Feature.objects.filter(slug=integration_slug)
                .values_list("is_enabled", flat=True)
                .first()
            )
            if integration_enabled is None:
                parameters.setdefault(parameter_key, "enabled")
            else:
                parameters[parameter_key] = (
                    "enabled" if bool(integration_enabled) else "disabled"
                )

        metadata["parameters"] = parameters
        suite_feature.metadata = metadata
        suite_feature.save(update_fields=["metadata", "updated_at"])

    # Keep the legacy feature rows to avoid cascading deletes of related
    # records (e.g. notes/tests) that may exist in long-lived environments.
    # Runtime gating now reads suite parameters, so these rows are effectively
    # deprecated but intentionally preserved for reversibility and data safety.


def reverse(apps, schema_editor):
    """Restore legacy integration feature flags from suite parameters."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")

    suite_feature = Feature.objects.filter(slug=SUITE_FEATURE_SLUG).first()
    metadata = _normalize_metadata(getattr(suite_feature, "metadata", {}))
    parameters = metadata.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}

    definitions = (
        (
            DEPLOYMENT_DISCOVERY_FEATURE_SLUG,
            "Odoo Sync: Deployment Discovery",
            "deployment_discovery",
        ),
        (
            EMPLOYEE_IMPORT_FEATURE_SLUG,
            "Odoo Sync: Employee Import",
            "employee_import",
        ),
        (
            EVERGO_USERS_FEATURE_SLUG,
            "Odoo Sync: Evergo Users",
            "evergo_users",
        ),
    )
    for slug, display, parameter_key in definitions:
        Feature.objects.update_or_create(
            slug=slug,
            defaults={
                "display": display,
                "source": "mainstream",
                "is_enabled": parameters.get(parameter_key, "enabled") == "enabled",
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0033_seed_odoo_crm_sync_features"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
