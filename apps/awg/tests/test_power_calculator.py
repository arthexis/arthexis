"""Tests for the electrical power calculator view."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from apps.awg.views.reports import electrical_power_calculator


class ElectricalPowerCalculatorTests(SimpleTestCase):
    """Validate input handling and computed values for power calculations."""

    def setUp(self):
        """Create request factory and reusable authenticated user stub."""

        self.factory = RequestFactory()
        self.user = SimpleNamespace(is_authenticated=True)

    def _post(self, data):
        """Submit a POST request to the wrapped calculator view."""

        request = self.factory.post("/awg/electrical-power/", data)
        request.user = self.user
        with patch("django.template.response.TemplateResponse.render", autospec=True) as render:
            render.side_effect = lambda response: response
            return electrical_power_calculator(request)

    def _get(self):
        """Submit a GET request to the wrapped calculator view."""

        request = self.factory.get("/awg/electrical-power/")
        request.user = self.user
        with patch("django.template.response.TemplateResponse.render", autospec=True) as render:
            render.side_effect = lambda response: response
            return electrical_power_calculator(request)

    def test_get_renders_calculator(self):
        """GET requests should render the electrical power calculator page."""

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.template_name, "awg/electrical_power_calculator.html")

    def test_post_calculates_single_phase_values(self):
        """Single-phase inputs should produce kVA, kW, and breaker values."""

        response = self._post(
            {
                "voltage": "240",
                "current": "30",
                "power_factor": "0.9",
                "phases": "1",
            }
        )

        self.assertEqual(response.status_code, 200)
        result = response.context_data["result"]
        self.assertEqual(result["kw"], Decimal("6.48"))
        self.assertEqual(result["recommended_breaker"], Decimal("37.50"))

    def test_post_rejects_oversized_numeric_inputs(self):
        """Huge numeric values should return a validation error instead of crashing."""

        response = self._post(
            {
                "voltage": "1000000001",
                "current": "20",
                "power_factor": "0.95",
                "phases": "3",
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context_data["error"],
            "Voltage and current are too large to calculate safely.",
        )

    def test_post_rejects_invalid_power_factor(self):
        """Power factor values outside 0-1 should return a user-facing error."""

        response = self._post(
            {
                "voltage": "208",
                "current": "50",
                "power_factor": "1.3",
                "phases": "3",
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context_data["error"],
            "Power factor must be between 0 and 1.",
        )
