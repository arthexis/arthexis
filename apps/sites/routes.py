"""Root route provider for public site and site-specific admin tools."""

from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic.base import RedirectView

from config.admin_urls import admin_route

from apps.sites import views as pages_views

ROOT_URLPATTERNS = [
    path(
        admin_route("service-worker.js"),
        pages_views.admin_service_worker,
        name="admin-service-worker",
    ),
    path(
        admin_route("user-tools/"),
        pages_views.admin_user_tools,
        name="admin-user-tools",
    ),
    path(
        admin_route("model-graph/<str:app_label>/"),
        admin.site.admin_view(pages_views.admin_model_graph),
        name="admin-model-graph",
    ),
    re_path(
        r"^(?P<lang>[a-z]{2})/$",
        RedirectView.as_view(url="/", permanent=True, query_string=True),
        name="legacy-language-prefix-root-redirect",
    ),
    re_path(
        r"^(?P<lang>[a-z]{2})/(?P<rest>[^/].*)$",
        RedirectView.as_view(url="/%(rest)s", permanent=True, query_string=True),
        name="legacy-language-prefix-redirect",
    ),
    path("", include("apps.sites.urls")),
]
