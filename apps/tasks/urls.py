from django.urls import path

from . import views

app_name = "tasks"

urlpatterns = [
    path("maintenance/request/", views.maintenance_request, name="maintenance-request"),
]
