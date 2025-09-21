from django.urls import path

from . import views

app_name = "awg"

urlpatterns = [
    path("", views.calculator, name="calculator"),
    path("energy-tariff/", views.energy_tariff_calculator, name="energy_tariff"),
]
