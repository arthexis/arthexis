"""Root route provider for public site and site-specific admin tools."""

from django.contrib import admin
from django.urls import include, path

from apps.sites import views as pages_views

ROOT_URLPATTERNS = [
    path(
        "admin/user-tools/",
        pages_views.admin_user_tools,
        name="admin-user-tools",
    ),
    path(
        "admin/model-graph/<str:app_label>/",
        admin.site.admin_view(pages_views.admin_model_graph),
        name="admin-model-graph",
    ),
    path("", include("apps.sites.urls")),
]
