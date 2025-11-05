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
from django.core.exceptions import AppRegistryNotReady, ImproperlyConfigured
from django.db.utils import DatabaseError, OperationalError, ProgrammingError
from django.urls import include, path
import teams.admin  # noqa: F401
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import RedirectView
from django.views.i18n import set_language
from django.utils.translation import gettext_lazy as _
from django.http import Http404
from core import views as core_views
from core.admindocs import (
    CommandsView,
    ModelGraphIndexView,
    OrderedModelIndexView,
)
from pages import views as pages_views

try:  # Gate optional GraphQL dependency for roles that do not install it
    from api.views import EnergyGraphQLView
except ModuleNotFoundError as exc:  # pragma: no cover - dependency intentionally optional
    if exc.name in {"graphene_django", "graphene"}:
        EnergyGraphQLView = None  # type: ignore[assignment]
    else:  # pragma: no cover - unrelated import error
        raise


def _graphql_feature_enabled() -> bool:
    """Return ``True`` when the GraphQL endpoint should be exposed."""

    if EnergyGraphQLView is None:
        return False

    try:
        from nodes.models import Node, NodeFeature
    except (ModuleNotFoundError, AppRegistryNotReady, ImproperlyConfigured):
        return True

    try:
        feature = NodeFeature.objects.filter(slug="graphql").first()
    except (DatabaseError, OperationalError, ProgrammingError):
        return True

    if feature is None:
        return True

    try:
        node = Node.get_local()
    except (DatabaseError, OperationalError, ProgrammingError):
        return True

    if node and not node.has_feature("graphql"):
        return False

    return True

admin.site.site_header = _("Constellation")
admin.site.site_title = _("Constellation")

# Apps that require a custom prefix for their URLs
URL_PREFIX_OVERRIDES = {"core": "api/rfid"}


def autodiscovered_urlpatterns():
    """Collect URL patterns from project apps automatically.

    Scans all installed apps located inside the project directory. If an app
    exposes a ``urls`` module, it is included under ``/<app_label>/`` unless a
    custom prefix is defined in :data:`URL_PREFIX_OVERRIDES`.
    """

    patterns = []
    base_dir = Path(settings.BASE_DIR).resolve()
    for app_config in apps.get_app_configs():
        app_path = Path(app_config.path).resolve()
        try:
            app_path.relative_to(base_dir)
        except ValueError:
            # Skip third-party apps outside of the project
            continue

        if app_config.label == "pages":
            # Root pages URLs are handled explicitly below
            continue

        module_name = f"{app_config.name}.urls"
        try:
            import_module(module_name)
        except ModuleNotFoundError:
            continue

        prefix = URL_PREFIX_OVERRIDES.get(app_config.label, app_config.label)
        patterns.append(path(f"{prefix}/", include(module_name)))

    return patterns


urlpatterns = [
    path(
        "admin/doc/manuals/",
        pages_views.admin_manual_list,
        name="django-admindocs-manuals",
    ),
    path(
        "admin/doc/manuals/<slug:slug>/",
        pages_views.admin_manual_detail,
        name="django-admindocs-manual-detail",
    ),
    path(
        "admin/doc/manuals/<slug:slug>/pdf/",
        pages_views.manual_pdf,
        name="django-admindocs-manual-pdf",
    ),
    path(
        "admin/doc/commands/",
        CommandsView.as_view(),
        name="django-admindocs-commands",
    ),
    path(
        "admin/doc/commands/",
        RedirectView.as_view(pattern_name="django-admindocs-commands"),
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
        "admin/core/todos/<int:pk>/focus/",
        core_views.todo_focus,
        name="todo-focus",
    ),
    path(
        "admin/core/todos/<int:pk>/done/",
        core_views.todo_done,
        name="todo-done",
    ),
    path(
        "admin/core/todos/<int:pk>/delete/",
        core_views.todo_delete,
        name="todo-delete",
    ),
    path(
        "admin/core/todos/<int:pk>/snapshot/",
        core_views.todo_snapshot,
        name="todo-snapshot",
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
    path("", include("pages.urls")),
]

_GRAPHQL_URLPOSITION = len(urlpatterns)


if EnergyGraphQLView is not None:

    class FeatureFlaggedGraphQLView(EnergyGraphQLView):
        """GraphQL endpoint guarded by the node feature flag."""

        def dispatch(self, request, *args, **kwargs):  # type: ignore[override]
            if not _graphql_feature_enabled():
                raise Http404()
            return super().dispatch(request, *args, **kwargs)

    urlpatterns.insert(
        _GRAPHQL_URLPOSITION,
        path("graphql/", FeatureFlaggedGraphQLView.as_view(), name="graphql"),
    )

urlpatterns += autodiscovered_urlpatterns()

if settings.DEBUG:
    try:
        import debug_toolbar
    except ModuleNotFoundError:  # pragma: no cover - optional dependency
        pass
    else:
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
