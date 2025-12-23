from django.urls import path

from .views import reports, requests

app_name = "awg"

urlpatterns = [
    path("calculate/", requests.awg_calculate, name="awg_calculate"),
    path("", requests.calculator, name="calculator"),
    path("zapped/", requests.zapped_result, name="zapped"),
    path("energy-tariff/", reports.energy_tariff_calculator, name="energy_tariff"),
]
