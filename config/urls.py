"""Project URL configuration reserved for framework-level routes."""

from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path
from django.views.generic.base import RedirectView

from apps.media.views import serve_media_file
from config.admin_urls import admin_route
from config.route_providers import autodiscovered_route_patterns

# Ensure admin registrations are loaded before URL resolution.
admin.autodiscover()

admin.site.site_header = settings.ADMIN_SITE_HEADER
admin.site.site_title = settings.ADMIN_SITE_TITLE
admin.site.index_title = settings.ADMIN_INDEX_TITLE

urlpatterns = autodiscovered_route_patterns()
urlpatterns += [
    path(admin_route(), admin.site.urls),
    path("admin/", RedirectView.as_view(url="/" + admin_route(), permanent=False)),
]

media_url_path = settings.MEDIA_URL.lstrip("/")
if (
    media_url_path
    and settings.MEDIA_URL.startswith("/")
    and not settings.MEDIA_URL.startswith("//")
):
    if not media_url_path.endswith("/"):
        media_url_path = f"{media_url_path}/"
    urlpatterns += [
        path(
            f"{media_url_path}<path:path>",
            serve_media_file,
            name="protected-media",
        ),
    ]

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
