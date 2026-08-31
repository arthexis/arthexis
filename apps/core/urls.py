from django.apps import apps as django_apps
from django.urls import path

from . import views

urlpatterns = [
    path("rfid-login/", views.rfid_login, name="rfid-login"),
    path("rfids/", views.rfid_batch, name="rfid-batch"),
    path(
        "usage-analytics/summary/",
        views.usage_analytics_summary,
        name="usage-analytics-summary",
    ),
]

if django_apps.is_installed("apps.odoo"):
    urlpatterns.extend(
        [
            path("products/", views.product_list, name="product-list"),
            path(
                "live-subscribe/",
                views.add_live_subscription,
                name="add-live-subscription",
            ),
            path(
                "live-list/",
                views.live_subscription_list,
                name="live-subscription-list",
            ),
        ]
    )
