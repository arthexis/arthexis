from django.urls import path

from .views import reports, requests

app_name = "awg"

urlpatterns = [
    path("calculate/", requests.awg_calculate, name="awg_calculate"),
    path("", requests.calculator, name="calculator"),
    path("zapped/", requests.zapped_result, name="zapped"),
    path("energy-tariff/", reports.energy_tariff_calculator, name="energy_tariff"),
    path("electrical-power/", reports.electrical_power_calculator, name="electrical_power"),
    path("ev-charging/", reports.ev_charging_calculator, name="ev_charging"),
    path(
        "mtg-hypergeometric/",
        reports.mtg_hypergeometric_calculator,
        name="mtg_hypergeometric",
    ),
]
