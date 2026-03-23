"""Canonical security-group names and helpers for the suite security model."""

from __future__ import annotations

SITE_OPERATOR_GROUP_NAME = "Site Operator"
NETWORK_OPERATOR_GROUP_NAME = "Network Operator"
PRODUCT_DEVELOPER_GROUP_NAME = "Product Developer"
RELEASE_MANAGER_GROUP_NAME = "Release Manager"
EXTERNAL_AGENT_GROUP_NAME = "External Agent"

STAFF_SECURITY_GROUP_NAMES: tuple[str, ...] = (
    SITE_OPERATOR_GROUP_NAME,
    NETWORK_OPERATOR_GROUP_NAME,
    PRODUCT_DEVELOPER_GROUP_NAME,
    RELEASE_MANAGER_GROUP_NAME,
    EXTERNAL_AGENT_GROUP_NAME,
)

LEGACY_STAFF_GROUP_NAME_MAP: dict[str, str] = {
    "Charge Station Manager": NETWORK_OPERATOR_GROUP_NAME,
    "Odoo User": EXTERNAL_AGENT_GROUP_NAME,
}


def is_staff_security_group_name(name: str | None) -> bool:
    """Return whether ``name`` is one of the canonical staff security groups."""

    return (name or "").strip() in STAFF_SECURITY_GROUP_NAMES
