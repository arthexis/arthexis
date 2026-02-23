"""URL routes for the operations app."""

from django.urls import path

from . import views

app_name = "ops"

urlpatterns = [
    path("clear-active/", views.clear_active_operation, name="clear-active"),
]
