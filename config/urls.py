"""Project URL configuration reserved for framework-level routes."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path
from django.views.decorators.csrf import csrf_exempt
from django.views.generic.base import RedirectView
from django.views.i18n import set_language

from config.admin_urls import admin_route
from config.route_providers import autodiscovered_route_patterns

# Ensure admin registrations are loaded before URL resolution.
admin.autodiscover()

admin.site.site_header = settings.ADMIN_SITE_HEADER
admin.site.site_title = settings.ADMIN_SITE_TITLE
admin.site.index_title = settings.ADMIN_INDEX_TITLE

urlpatterns = [
    path("i18n/setlang/", csrf_exempt(set_language), name="set_language"),
]

urlpatterns += autodiscovered_route_patterns()
urlpatterns += [
    path(admin_route(), admin.site.urls),
    path("admin/", RedirectView.as_view(url="/" + admin_route(), permanent=False)),
    path("admindocs/", include("django.contrib.admindocs.urls")),
]

# Backward-compatible alias expected by legacy tests/imports.
autodiscovered_urlpatterns = autodiscovered_route_patterns

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
