"""Transitional imports for generic admin mixins still hosted in apps.core."""

from apps.core.admin.mixins import (
    OwnableAdminMixin,
    ProfileAdminMixin,
    SaveBeforeChangeAction,
)

__all__ = ["OwnableAdminMixin", "ProfileAdminMixin", "SaveBeforeChangeAction"]
