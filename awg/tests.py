from django.test import TestCase
from django.urls import reverse


class AWGCalculatorTests(TestCase):
    def test_page_renders_and_calculates(self):
        url = reverse("awg:calculator")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "<form")
        resp = self.client.get(url, {"awg_size": "4", "material": "cu", "line_num": "1"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "5.189")  # diameter in mm for AWG 4 Cu
        self.assertContains(resp, "95")  # 90C ampacity
        self.assertContains(resp, "Conduit Fill")
        self.assertContains(resp, "EMT")
