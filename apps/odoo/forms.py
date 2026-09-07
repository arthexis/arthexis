"""Compatibility bridge for Odoo admin forms during core UI extraction."""

from apps.core.admin.forms import OdooEmployeeAdminForm, OdooProductAdminForm

__all__ = ["OdooEmployeeAdminForm", "OdooProductAdminForm"]
