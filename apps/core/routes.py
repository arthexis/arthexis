"""Root route provider for core-owned framework extensions."""

from django.urls import include, path
from django.views.generic import RedirectView

from config.admin_urls import admin_route

from apps.core import views as core_views
from apps.core.admindocs import (
    CommandsView,
    ModelDetailDocsView,
    ModelGraphIndexView,
    OrderedModelIndexView,
)

ROOT_URLPATTERNS = [
    path("core/", include("apps.core.urls")),
    path(
        admin_route("doc/commands/"),
        RedirectView.as_view(pattern_name="django-admindocs-commands"),
    ),
    path(
        "admindocs/commands/",
        CommandsView.as_view(),
        name="django-admindocs-commands",
    ),
    path(
        admin_route("doc/model-graphs/"),
        ModelGraphIndexView.as_view(),
        name="django-admindocs-model-graphs",
    ),
    path(
        "admindocs/model-graphs/",
        RedirectView.as_view(pattern_name="django-admindocs-model-graphs"),
    ),
    path(
        "admindocs/models/",
        OrderedModelIndexView.as_view(),
        name="django-admindocs-models-index",
    ),
    path(
        "admindocs/models/<str:app_label>/<str:model_name>/",
        ModelDetailDocsView.as_view(),
        name="django-admindocs-models-detail",
    ),
    path(
        admin_route("doc/"),
        RedirectView.as_view(pattern_name="django-admindocs-docroot"),
    ),
    path("version/", core_views.version_info, name="version-info"),
    path(
        admin_route("core/releases/<int:pk>/<str:action>/"),
        core_views.release_progress,
        name="release-progress",
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
    path(
        admin_route("request-temp-password/"),
        core_views.request_temp_password,
        name="admin-request-temp-password",
    ),
]
