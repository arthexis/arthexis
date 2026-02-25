"""Tests for the EV charging session calculator view."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from apps.awg.views.reports import ev_charging_calculator


class EvChargingCalculatorTests(SimpleTestCase):
    """Validate EV charging estimates and input validation paths."""

    def setUp(self):
        """Create request factory and reusable authenticated user stub."""

        self.factory = RequestFactory()
        self.user = SimpleNamespace(is_authenticated=True)

    def _post(self, data):
        """Submit a POST request to the wrapped EV calculator view."""

        request = self.factory.post("/awg/ev-charging/", data)
        request.user = self.user
        with patch("django.template.response.TemplateResponse.render", autospec=True) as render:
            render.side_effect = lambda instance: instance
            return ev_charging_calculator(request)

    def _get(self):
        """Submit a GET request to the wrapped EV calculator view."""

        request = self.factory.get("/awg/ev-charging/")
        request.user = self.user
        with patch("django.template.response.TemplateResponse.render", autospec=True) as render:
            render.side_effect = lambda instance: instance
            return ev_charging_calculator(request)

    def test_get_renders_calculator(self):
        """GET requests should render the EV charging calculator page."""

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.template_name, "awg/ev_charging_calculator.html")

    def test_post_calculates_ev_totals_with_tariff(self):
        """Valid inputs should compute charge time and optional tariff cost."""

        response = self._post(
            {
                "battery_kwh": "77",
                "start_soc": "20",
                "target_soc": "80",
                "charger_power_kw": "11",
                "charging_efficiency": "0.9",
                "tariff_mxn_kwh": "3.2",
            }
        )

        self.assertEqual(response.status_code, 200)
        result = response.context_data["result"]
        self.assertEqual(result["battery_energy_needed"], Decimal("46.20"))
        self.assertEqual(result["wall_energy_needed"], Decimal("51.33"))
        self.assertEqual(result["charging_time_hours"], Decimal("4.67"))
        self.assertEqual(result["estimated_cost_mxn"], Decimal("164.26"))

    def test_post_rejects_invalid_soc_window(self):
        """Target SOC must be greater than starting SOC."""

        response = self._post(
            {
                "battery_kwh": "60",
                "start_soc": "80",
                "target_soc": "70",
                "charger_power_kw": "7.2",
                "charging_efficiency": "0.92",
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context_data["error"], "Target SOC must be greater than start SOC.")

    def test_post_rejects_invalid_efficiency(self):
        """Efficiency must stay in the inclusive-exclusive interval (0, 1]."""

        response = self._post(
            {
                "battery_kwh": "60",
                "start_soc": "10",
                "target_soc": "70",
                "charger_power_kw": "7.2",
                "charging_efficiency": "1.2",
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context_data["error"],
            "Charging efficiency must be greater than 0 and at most 1.",
        )
