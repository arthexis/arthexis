"""Root route provider for core-owned framework extensions."""

from django.apps import apps as django_apps
from django.urls import include, path

from apps.core import views as core_views
from config.admin_urls import admin_route

ROOT_URLPATTERNS = [
    path("core/", include("apps.core.urls")),
    path("version/", core_views.version_info, name="version-info"),
    path("core/impersonation/stop/", core_views.stop_impersonation, name="stop-impersonation"),
    path(
        admin_route("core/releases/<int:pk>/<str:action>/"),
        core_views.release_progress,
        name="release-progress",
    ),
    path(
        admin_route("request-temp-password/"),
        core_views.request_temp_password,
        name="admin-request-temp-password",
    ),
]

if django_apps.is_installed("apps.odoo"):
    ROOT_URLPATTERNS.extend(
        [
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
    )
