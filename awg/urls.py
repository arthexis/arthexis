from django.urls import path

from . import views

app_name = "awg"

urlpatterns = [
    path("", views.calculator, name="calculator"),
    path("future-event/", views.future_event_calculator, name="future_event"),
    path("zapped/", views.zapped_result, name="zapped"),
    path("energy-tariff/", views.energy_tariff_calculator, name="energy_tariff"),
]
