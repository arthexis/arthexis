from django.urls import path

from .views import health, ingestion_events

urlpatterns = [
    path("health/", health, name="rpiconnect-health"),
    path("ingestion/events/", ingestion_events, name="rpiconnect-ingestion-events"),
]
