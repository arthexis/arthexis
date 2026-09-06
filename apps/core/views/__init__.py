"""Core view exports with lazy compatibility for moved domain UI."""

from __future__ import annotations

from importlib import import_module

from django.apps import apps as django_apps

from .admin_tools import version_info
from .usage_analytics import usage_analytics_summary

_LAZY_EXPORTS = {
    "DirtyRepository": ("apps.release.publishing.exceptions", "DirtyRepository"),
    "PublishPending": ("apps.release.publishing.exceptions", "PublishPending"),
    "PUBLISH_STEPS": ("apps.release.views", "PUBLISH_STEPS"),
    "_append_log": ("apps.reports.views.logs", "_append_log"),
    "_release_log_name": ("apps.reports.views.logs", "_release_log_name"),
    "_resolve_release_log_dir": ("apps.reports.views.logs", "_resolve_release_log_dir"),
    "release_progress": ("apps.release.views", "release_progress"),
    "request_temp_password": ("apps.users.admin_views", "request_temp_password"),
    "rfid_batch": ("apps.cards.core_views", "rfid_batch"),
    "rfid_login": ("apps.cards.auth_views", "rfid_login"),
    "stop_impersonation": ("apps.users.admin_views", "stop_impersonation"),
    "add_live_subscription": ("apps.odoo.core_views", "add_live_subscription"),
    "live_subscription_list": ("apps.odoo.core_views", "live_subscription_list"),
    "odoo_products": ("apps.odoo.core_views", "odoo_products"),
    "odoo_quote_report": ("apps.odoo.core_views", "odoo_quote_report"),
    "product_list": ("apps.odoo.core_views", "product_list"),
}

_ODOO_EXPORTS = {
    "add_live_subscription",
    "live_subscription_list",
    "odoo_products",
    "odoo_quote_report",
    "product_list",
}

__all__ = [
    "DirtyRepository",
    "PublishPending",
    "PUBLISH_STEPS",
    "_append_log",
    "_release_log_name",
    "_resolve_release_log_dir",
    "release_progress",
    "request_temp_password",
    "rfid_batch",
    "rfid_login",
    "stop_impersonation",
    "usage_analytics_summary",
    "version_info",
]

if django_apps.is_installed("apps.odoo"):
    __all__.extend(sorted(_ODOO_EXPORTS))


def __getattr__(name: str):
    if name in _ODOO_EXPORTS and not django_apps.is_installed("apps.odoo"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
