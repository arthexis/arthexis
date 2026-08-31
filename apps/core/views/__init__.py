from __future__ import annotations

from django.apps import apps as django_apps

from .admin_tools import request_temp_password, stop_impersonation, version_info
from .auth import rfid_login
from .reports import (
    PUBLISH_STEPS,
    DirtyRepository,
    PublishPending,
    _append_log,
    _release_log_name,
    _resolve_release_log_dir,
    release_progress,
)
from .rfid import rfid_batch
from .usage_analytics import usage_analytics_summary

if django_apps.is_installed("apps.odoo"):
    from .odoo import (
        add_live_subscription,
        live_subscription_list,
        odoo_products,
        odoo_quote_report,
        product_list,
    )

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
    __all__.extend(
        [
            "add_live_subscription",
            "live_subscription_list",
            "odoo_products",
            "odoo_quote_report",
            "product_list",
        ]
    )
