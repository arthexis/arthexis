"""Root route provider for app-owned URL mounts."""

from django.urls import include, path

from config.admin_urls import admin_route

from . import core_views

ROOT_URLPATTERNS = [
    path("odoo/", include("apps.odoo.urls")),
    # Legacy core API/admin paths retained while ownership moves to apps.odoo.
    path("core/products/", core_views.product_list, name="product-list"),
    path(
        "core/live-subscribe/",
        core_views.add_live_subscription,
        name="add-live-subscription",
    ),
    path(
        "core/live-list/",
        core_views.live_subscription_list,
        name="live-subscription-list",
    ),
    path(
        admin_route("core/odoo-products/"),
        core_views.odoo_products,
        name="odoo-products",
    ),
    path(
        admin_route("core/odoo-quote-report/"),
        core_views.odoo_quote_report,
        name="odoo-quote-report",
    ),
]
