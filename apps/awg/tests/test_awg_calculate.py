from django.test import TestCase
from django.urls import reverse

from apps.awg.models import CableSize, CalculatorTemplate


class AwgCalculateViewTests(TestCase):
    def setUp(self):
        CableSize.objects.create(
            awg_size="4",
            material="cu",
            dia_in=0.1,
            dia_mm=2.5,
            area_kcmil=21.1,
            area_mm2=21.1,
            k_ohm_km=0.1,
            k_ohm_kft=0.0328,
            amps_60c=95,
            amps_75c=105,
            amps_90c=115,
            line_num=1,
        )

    def test_missing_meters_rejected(self):
        url = reverse("awg:awg_calculate")
        response = self.client.get(
            url, {"amps": 40, "volts": 220, "material": "cu"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("meters", response.json()["error"].lower())

    def test_calculates_from_parameters(self):
        url = reverse("awg:awg_calculate")
        response = self.client.get(
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
        data = response.json()
        self.assertIn("awg", data)
        self.assertNotEqual(data["awg"], "n/a")

    def test_template_supplies_defaults(self):
        template = CalculatorTemplate.objects.create(
            name="EV Charger",
            meters=20,
            amps=50,
            volts=220,
            material="cu",
            max_lines=1,
            phases=2,
            ground=1,
        )
        url = reverse("awg:awg_calculate")
        response = self.client.get(url, {"template": template.pk})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("awg", data)
        self.assertNotEqual(data["awg"], "n/a")

        override = self.client.get(url, {"template": template.pk, "amps": 30})
        self.assertEqual(override.status_code, 200)
        self.assertIn("awg", override.json())

    def test_unknown_template_returns_not_found(self):
        url = reverse("awg:awg_calculate")
        response = self.client.get(url, {"template": 9999})

        self.assertEqual(response.status_code, 404)
