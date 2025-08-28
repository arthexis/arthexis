"""Project URL configuration with automatic app discovery.

This module includes URL patterns from any installed application that exposes
an internal ``urls`` module. This allows new apps with URL configurations to be
added without editing this file, except for top-level routes such as the admin
interface or the main website.
"""

from importlib import import_module
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.decorators.csrf import csrf_exempt
from django.views.i18n import set_language
from django.utils.translation import gettext_lazy as _

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

        if app_config.label == "website":
            # Root website URLs are handled explicitly below
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
    path("admin/doc/", include("django.contrib.admindocs.urls")),
    path("admin/", admin.site.urls),
    path("i18n/setlang/", csrf_exempt(set_language), name="set_language"),
    path("", include("website.urls")),
]

urlpatterns += autodiscovered_urlpatterns()

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

