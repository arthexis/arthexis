from django.urls import path

from apps.ocpp.views import health

urlpatterns = [path("health/", health, name="health")]
