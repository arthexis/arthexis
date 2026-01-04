from django.urls import path

from . import views

app_name = "mcp_api"

urlpatterns = [
    path("servers/<slug:slug>/manifest/", views.server_manifest, name="mcp_api_manifest"),
    path("servers/<slug:slug>/rotate-secret/", views.rotate_secret, name="mcp_api_rotate_secret"),
]
