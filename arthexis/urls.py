"""Root URL configuration."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path, re_path

from arthexis.markdown_site import markdown_document, markdown_home


def health(request):
    """Return a dependency-light HTTP liveness response."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("", markdown_home, name="markdown-home"),
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
    path("ocpp/", include("apps.ocpp.urls")),
    re_path(r"^(?P<document>.+\.md)$", markdown_document, name="markdown-document"),
]
