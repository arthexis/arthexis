from __future__ import annotations


def odoo_sync_metadata(
    *,
    deployment_discovery: str = "enabled",
    employee_import: str = "enabled",
    evergo_users: str = "enabled",
) -> dict[str, dict[str, str]]:
    """Build Odoo CRM sync metadata parameters for tests."""

    return {
        "parameters": {
            "deployment_discovery": deployment_discovery,
            "employee_import": employee_import,
            "evergo_users": evergo_users,
        }
    }
