"""URL routes for the Google app."""

from django.urls import path

from .views import discover_sheet

app_name = "google"

urlpatterns = [
    path("sheets/discover/", discover_sheet, name="sheets-discover"),
]
