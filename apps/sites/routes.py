"""Root route provider for public site and site-specific admin tools."""

from django.contrib import admin
from django.urls import include, path

from config.admin_urls import admin_route

from apps.sites import views as pages_views

ROOT_URLPATTERNS = [
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
    path("", include("apps.sites.urls")),
]
