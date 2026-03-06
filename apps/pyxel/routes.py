"""Root route provider for Pyxel admin launch actions."""

from django.contrib import admin
from django.urls import path

from config.admin_urls import admin_route

from apps.pyxel import admin_views

ROOT_URLPATTERNS = [
    path(
        admin_route("pyxel/live-stats/"),
        admin.site.admin_view(admin_views.open_live_stats_view),
        name="admin-pyxel-live-stats",
    ),
    path(
        admin_route("pyxel/open-viewport/"),
        admin.site.admin_view(admin_views.open_viewport_view),
        name="admin-pyxel-open-viewport",
    ),
    path(
        admin_route("pyxel/open-viewport/<int:pk>/"),
        admin.site.admin_view(admin_views.open_viewport_view),
        name="admin-pyxel-open-viewport-specific",
    ),
]
