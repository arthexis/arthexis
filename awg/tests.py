from django.test import TestCase
from django.urls import reverse


class AWGCalculatorTests(TestCase):
    def test_page_renders_and_calculates(self):
        url = reverse("awg:calculator")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "<form")

        data = {
            "meters": "10",
            "amps": "40",
            "volts": "220",
            "material": "cu",
            "max_lines": "1",
            "phases": "2",
            "temperature": "60",
            "conduit": "emt",
            "ground": "1",
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "<table")
        self.assertContains(resp, "AWG Size</th>")
        self.assertContains(resp, "8")
        self.assertContains(resp, "Voltage Drop")
        self.assertContains(resp, "EMT")

    def test_no_cable_found(self):
        url = reverse("awg:calculator")
        data = {
            "meters": "1000",
            "amps": "200",
            "volts": "220",
            "material": "cu",
            "max_lines": "1",
            "phases": "2",
            "temperature": "60",
            "conduit": "emt",
            "ground": "1",
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No Suitable Cable Found")
