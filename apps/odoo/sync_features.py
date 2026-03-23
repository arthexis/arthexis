"""Feature-flag helpers for Odoo CRM sync integrations."""

from __future__ import annotations

from apps.features.models import Feature
from apps.features.parameters import get_feature_parameter_value
from apps.features.utils import is_suite_feature_enabled

ODOO_CRM_SYNC_SUITE_FEATURE_SLUG = "odoo-crm-sync"
ODOO_SYNC_DEPLOYMENT_DISCOVERY_PARAMETER_KEY = "deployment_discovery"
ODOO_SYNC_EMPLOYEE_IMPORT_PARAMETER_KEY = "employee_import"
ODOO_SYNC_EVERGO_USERS_PARAMETER_KEY = "evergo_users"


def is_odoo_sync_integration_enabled(
    integration_key: str,
    *,
    default: bool = False,
) -> bool:
    """Return whether the Odoo CRM sync suite and integration toggles are enabled."""

    suite_enabled = is_suite_feature_enabled(
        ODOO_CRM_SYNC_SUITE_FEATURE_SLUG,
        default=default,
    )
    if not suite_enabled:
        return False
    suite_feature = Feature.objects.filter(slug=ODOO_CRM_SYNC_SUITE_FEATURE_SLUG).only(
        "metadata"
    ).first()
    default_state = "enabled" if default else "disabled"
    integration_state = get_feature_parameter_value(
        suite_feature,
        integration_key,
        default=default_state,
    )
    return integration_state == "enabled"


__all__ = [
    "ODOO_CRM_SYNC_SUITE_FEATURE_SLUG",
    "ODOO_SYNC_DEPLOYMENT_DISCOVERY_PARAMETER_KEY",
    "ODOO_SYNC_EMPLOYEE_IMPORT_PARAMETER_KEY",
    "ODOO_SYNC_EVERGO_USERS_PARAMETER_KEY",
    "is_odoo_sync_integration_enabled",
]
