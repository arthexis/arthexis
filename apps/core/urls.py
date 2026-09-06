"""Compatibility URL module for the former ``core/`` include.

The root route provider no longer mounts this module.  It remains importable for
third-party callers while delegating domain endpoints to their owner apps.
"""

from django.apps import apps as django_apps
from django.urls import path

from apps.cards.auth_views import rfid_login
from apps.cards.core_views import rfid_batch
from apps.core.views.usage_analytics import usage_analytics_summary

urlpatterns = [
    path("rfid-login/", rfid_login, name="rfid-login"),
    path("rfids/", rfid_batch, name="rfid-batch"),
    path(
        "usage-analytics/summary/",
        usage_analytics_summary,
        name="usage-analytics-summary",
    ),
]

if django_apps.is_installed("apps.odoo"):
    from apps.odoo.core_views import (
        add_live_subscription,
        live_subscription_list,
        product_list,
    )

    urlpatterns.extend(
        [
            path("products/", product_list, name="product-list"),
            path(
                "live-subscribe/",
                add_live_subscription,
                name="add-live-subscription",
            ),
            path(
                "live-list/",
                live_subscription_list,
                name="live-subscription-list",
            ),
        ]
    )
