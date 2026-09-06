"""Compatibility bridge for shared admin mixins used by Odoo admin."""

from apps.core.admin.mixins import (
    OwnableAdminMixin,
    ProfileAdminMixin,
    SaveBeforeChangeAction,
    _build_credentials_actions,
)

__all__ = [
    "OwnableAdminMixin",
    "ProfileAdminMixin",
    "SaveBeforeChangeAction",
    "_build_credentials_actions",
]
