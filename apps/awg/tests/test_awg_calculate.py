import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from apps.awg.views.requests import awg_calculate


class AwgCalculateViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _get(self, url, data):
        request = self.factory.get(url, data)
        return awg_calculate(request)

    def _json(self, response):
        return json.loads(response.content.decode("utf-8"))

    def test_missing_meters_rejected(self):
        url = reverse("awg:awg_calculate")
        with patch("apps.awg.views.requests.find_awg") as find_awg:
            response = self._get(url, {"amps": 40, "volts": 220, "material": "cu"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("meters", self._json(response)["error"].lower())
        find_awg.assert_not_called()

    def test_calculates_from_parameters(self):
        url = reverse("awg:awg_calculate")
        with patch(
            "apps.awg.views.requests.find_awg", return_value={"awg": "4"}
        ) as find_awg:
            response = self._get(
                url,
                {
                    "meters": 10,
                    "amps": 40,
                    "volts": 220,
                    "material": "cu",
                    "max_lines": 1,
                    "phases": 2,
                    "ground": 1,
                },
            )

        self.assertEqual(response.status_code, 200)
        data = self._json(response)
        self.assertIn("awg", data)
        self.assertNotEqual(data["awg"], "n/a")
        find_awg.assert_called_once()
        self.assertEqual(
            find_awg.call_args.kwargs,
            {
                "meters": "10",
                "amps": "40",
                "volts": "220",
                "material": "cu",
                "max_lines": "1",
                "phases": "2",
                "ground": "1",
            },
        )

    def test_template_supplies_defaults(self):
        template = SimpleNamespace(
            name="EV Charger",
            meters=20,
            amps=50,
            volts=220,
            material="cu",
            max_lines=1,
            phases=2,
            ground=1,
            max_awg=None,
            temperature=None,
            conduit=None,
        )
        url = reverse("awg:awg_calculate")
        with patch(
            "apps.awg.views.requests.CalculatorTemplate"
        ) as calculator_template, patch(
            "apps.awg.views.requests.find_awg", return_value={"awg": "4"}
        ) as find_awg:
            calculator_template.objects.filter.return_value.first.return_value = (
                template
            )
            response = self._get(url, {"template": "EV Charger"})

        self.assertEqual(response.status_code, 200)
        data = self._json(response)
        self.assertIn("awg", data)
        self.assertNotEqual(data["awg"], "n/a")
        find_awg.assert_called_once()
        self.assertEqual(
            find_awg.call_args.kwargs,
            {
                "meters": 20,
                "amps": 50,
                "volts": 220,
                "material": "cu",
                "max_lines": 1,
                "phases": 2,
                "ground": 1,
            },
        )

        with patch(
            "apps.awg.views.requests.CalculatorTemplate"
        ) as calculator_template, patch(
            "apps.awg.views.requests.find_awg", return_value={"awg": "4"}
        ) as find_awg:
            calculator_template.objects.filter.return_value.first.return_value = (
                template
            )
            override = self._get(url, {"template": "EV Charger", "amps": 30})
        self.assertEqual(override.status_code, 200)
        self.assertIn("awg", self._json(override))
        find_awg.assert_called_once()
        self.assertEqual(
            find_awg.call_args.kwargs,
            {
                "meters": 20,
                "amps": "30",
                "volts": 220,
                "material": "cu",
                "max_lines": 1,
                "phases": 2,
                "ground": 1,
            },
        )

    def test_unknown_template_returns_not_found(self):
        url = reverse("awg:awg_calculate")
        with patch("apps.awg.views.requests.CalculatorTemplate") as calculator_template:
            calculator_template.objects.filter.return_value.first.return_value = None
            response = self._get(url, {"template": 9999})

        self.assertEqual(response.status_code, 404)
