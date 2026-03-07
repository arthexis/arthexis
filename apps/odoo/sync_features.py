"""Feature-flag helpers for Odoo CRM sync integrations."""

from __future__ import annotations

from apps.features.utils import is_suite_feature_enabled

ODOO_CRM_SYNC_SUITE_FEATURE_SLUG = "odoo-crm-sync"
ODOO_SYNC_DEPLOYMENT_DISCOVERY_FEATURE_SLUG = "odoo-sync-deployment-discovery"
ODOO_SYNC_EMPLOYEE_IMPORT_FEATURE_SLUG = "odoo-sync-employee-import"
ODOO_SYNC_EVERGO_USERS_FEATURE_SLUG = "odoo-sync-evergo-users"


def is_odoo_sync_integration_enabled(integration_slug: str, *, default: bool = False) -> bool:
    """Return whether the Odoo CRM sync suite and integration toggles are enabled."""

    suite_enabled = is_suite_feature_enabled(
        ODOO_CRM_SYNC_SUITE_FEATURE_SLUG,
        default=default,
    )
    if not suite_enabled:
        return False
    return is_suite_feature_enabled(integration_slug, default=default)


__all__ = [
    "ODOO_CRM_SYNC_SUITE_FEATURE_SLUG",
    "ODOO_SYNC_DEPLOYMENT_DISCOVERY_FEATURE_SLUG",
    "ODOO_SYNC_EMPLOYEE_IMPORT_FEATURE_SLUG",
    "ODOO_SYNC_EVERGO_USERS_FEATURE_SLUG",
    "is_odoo_sync_integration_enabled",
]
