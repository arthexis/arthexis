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
            render.side_effect = lambda instance: instance
            return electrical_power_calculator(request)

    def _get(self):
        """Submit a GET request to the wrapped calculator view."""

        request = self.factory.get("/awg/electrical-power/")
        request.user = self.user
        with patch("django.template.response.TemplateResponse.render", autospec=True) as render:
            render.side_effect = lambda instance: instance
            return electrical_power_calculator(request)

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

    def test_post_calculates_three_phase_values(self):
        """Three-phase inputs should exercise the sqrt(3) calculation path."""

        response = self._post(
            {
                "voltage": "208",
                "current": "30",
                "power_factor": "0.85",
                "phases": "3",
            }
        )

        self.assertEqual(response.status_code, 200)
        result = response.context_data["result"]
        self.assertEqual(result["kw"], Decimal("9.19"))
        self.assertEqual(result["recommended_breaker"], Decimal("37.50"))
