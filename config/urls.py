"""Project URL configuration with automatic app discovery.

This module includes URL patterns from any installed application that exposes
an internal ``urls`` module. This allows new apps with URL configurations to be
added without editing this file, except for top-level routes such as the admin
interface or the main pages.
"""

from importlib import import_module
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.contrib import admin
from django.urls import include, path
from apps.nodes import admin as nodes_admin  # noqa: F401 - ensure custom admin URLs are registered
from apps.odoo import admin as odoo_admin  # noqa: F401
from apps.tasks import admin as tasks_admin  # noqa: F401
from apps.teams import admin as teams_admin  # noqa: F401
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import RedirectView
from django.views.i18n import set_language
from django.utils.translation import gettext_lazy as _
from apps.core import views as core_views
from apps.core.admindocs import (
    CommandsView,
    ModelGraphIndexView,
    OrderedModelIndexView,
)
from apps.sites import views as pages_views

# Ensure admin registrations (e.g., OCPP chargers) are loaded before URL
# resolution to avoid missing admin views such as /admin/ocpp/charger/.
admin.autodiscover()

admin.site.site_header = _("Constellation")
admin.site.site_title = _("Constellation")


def autodiscovered_urlpatterns():
    """Collect URL patterns from project apps automatically.

    Scans all installed apps located inside the project directory. If an app
    exposes a ``urls`` module, it is included under ``/<app_label>/``. Any
    optional ``api.urls`` module is included under ``/<app_label>/api/`` to keep
    APIs scoped to their own application namespace.
    """

    def include_if_exists(app_config, module_suffix, prefix):
        module_name = f"{app_config.name}.{module_suffix}"
        try:
            import_module(module_name)
        except ModuleNotFoundError:
            return None
        return path(prefix, include(module_name))

    patterns = []
    base_dir = Path(settings.BASE_DIR).resolve()
    for app_config in apps.get_app_configs():
        app_path = Path(app_config.path).resolve()
        try:
            app_path.relative_to(base_dir)
        except ValueError:
            # Skip third-party apps outside of the project
            continue

        if app_config.label in {"pages", "docs"}:
            # Root pages URLs are handled explicitly below
            continue

        urls_pattern = include_if_exists(app_config, "urls", f"{app_config.label}/")
        if urls_pattern:
            patterns.append(urls_pattern)

        api_pattern = include_if_exists(app_config, "api.urls", f"{app_config.label}/api/")
        if api_pattern:
            patterns.append(api_pattern)

    return patterns


urlpatterns = [
    path(
        "admin/user-tools/",
        pages_views.admin_user_tools,
        name="admin-user-tools",
    ),
    path(
        "admin/doc/commands/",
        CommandsView.as_view(),
        name="django-admindocs-commands",
    ),
    path(
        "admin/doc/model-graphs/",
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
    path("admindocs/", include("django.contrib.admindocs.urls")),
    path(
        "admin/doc/",
        RedirectView.as_view(pattern_name="django-admindocs-docroot"),
    ),
    path(
        "admin/model-graph/<str:app_label>/",
        admin.site.admin_view(pages_views.admin_model_graph),
        name="admin-model-graph",
    ),
    path("version/", core_views.version_info, name="version-info"),
    path(
        "admin/core/releases/<int:pk>/<str:action>/",
        core_views.release_progress,
        name="release-progress",
    ),
    path(
        "admin/core/odoo-products/",
        core_views.odoo_products,
        name="odoo-products",
    ),
    path(
        "admin/core/odoo-quote-report/",
        core_views.odoo_quote_report,
        name="odoo-quote-report",
    ),
    path(
        "admin/request-temp-password/",
        core_views.request_temp_password,
        name="admin-request-temp-password",
    ),
    path("admin/", admin.site.urls),
    path("i18n/setlang/", csrf_exempt(set_language), name="set_language"),
    path("", include("apps.docs.urls")),
    path("", include("apps.sites.urls")),
]

urlpatterns += autodiscovered_urlpatterns()

if settings.DEBUG:
    if settings.HAS_DEBUG_TOOLBAR:
        urlpatterns = [
            path(
                "__debug__/",
                include(
                    ("debug_toolbar.urls", "debug_toolbar"), namespace="debug_toolbar"
                ),
            )
        ] + urlpatterns

    urlpatterns += staticfiles_urlpatterns()
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
