"""URL endpoints for client shortcut runtime."""

from django.urls import path

from . import views

app_name = "shortcuts"

urlpatterns = [
    path("client/config/", views.client_shortcut_config, name="client-config"),
    path("client/execute/<int:shortcut_id>/", views.execute_client_shortcut_view, name="client-execute"),
]
