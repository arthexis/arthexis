from django.urls import path

from . import views

app_name = "maps"

urlpatterns = [
    path("evcs/", views.charge_point_map, name="charge-point-map"),
]
